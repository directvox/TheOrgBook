import json
from api.indy.agent import Agent
import logging
from api.indy import eventloop
from rest_framework.exceptions import NotAcceptable


class ProofRequestProcesser(object):
    """
    Parses a proof request and constructs a proof.

    Does not yet support predicates.
    """

    def __init__(self, proofRequestWithFilters) -> None:
        self.__orgbook = Agent()
        self.__logger = logging.getLogger(__name__)
        self.__proof_request = json.loads(proofRequestWithFilters)[
            'proof_request']
        self.__filters = json.loads(proofRequestWithFilters)['filters'] \
            if 'filters' in json.loads(proofRequestWithFilters) \
            else {}

    async def __ConstructProof(self):
        self.__logger.debug("Constructing Proof ...")

        # We keep a reference to schemas that we discover and retrieve from the
        # ledger. We will need these again later.
        schemas = {'by_key': {}}

        # The client is sending the proof request in an upcoming format.
        # This shim allows Permitify to declare its proof requests format
        # in the latest format. Once von-agent is update to support the new
        # format, this shim can be removed.
        for attr in self.__proof_request['requested_attrs']:
            # new format expects restrictions with "schema_key"
            # Current format simply wants the seq_no of schema
            schema_key = self.__proof_request['requested_attrs'][
                attr]['restrictions'][0]['schema_key']

            # Ugly cache for now...
            if '%s::%s::%s' % (
                    schema_key['did'],
                    schema_key['name'],
                    schema_key['version']) in schemas['by_key']:
                schema = schemas['by_key']['%s::%s::%s' % (
                    schema_key['did'],
                    schema_key['name'],
                    schema_key['version'])]
            else:
                # Not optimal. von-agent should cache this.
                schema_json = await self.__orgbook.get_schema(
                    schema_key['did'],
                    schema_key['name'],
                    schema_key['version']
                )
                schema = json.loads(schema_json)

            schemas[schema['seqNo']] = schema
            schemas['by_key']['%s::%s::%s' % (
                schema_key['did'],
                schema_key['name'],
                schema_key['version'])] = schema

            self.__proof_request['requested_attrs'][
                attr]['schema_seq_no'] = schema['seqNo']
            del self.__proof_request['requested_attrs'][attr]['restrictions']

        self.__logger.debug(self.__proof_request)

        # Get claims for proof request from wallet
        claims = await self.__orgbook.get_claims(
            json.dumps(self.__proof_request))
        claims = json.loads(claims[1])

        self.__logger.debug(
            'Wallet returned the following claims for proof request: %s' %
            json.dumps(claims))

        # If any of the claims for proof are empty, we cannot construct a proof
        for attr in claims['attrs']:
            if not claims['attrs'][attr]:
                raise NotAcceptable('No claims found for attr %s' % attr)

        def get_claim_by_filter(clms, key, value):
            for clm in clms:
                if clm["attrs"][key] == value:
                    return clm
            raise NotAcceptable(
                'No claims found for filter %s = %s' % (
                    key, value))

        requested_claims = {
            'self_attested_attributes': {},
            'requested_attrs': {
                attr: [
                    # Either we get the first claim found
                    # by the provided filter
                    get_claim_by_filter(
                        claims["attrs"][attr],
                        attr,
                        self.__filters[attr])["claim_uuid"]
                    # Or we use the first claim found
                    if attr in self.__filters
                    else claims["attrs"][attr][0]["claim_uuid"],
                    True
                ]
                for attr in claims["attrs"]
            },
            'requested_predicates': {}
        }

        self.__logger.debug(
            'Built requested claims: %s' %
            json.dumps(requested_claims))

        # Build schemas json
        schemas = {
            claims["attrs"][attr][0]['claim_uuid']:
                schemas[claims["attrs"][attr][0]["schema_seq_no"]]
            for attr in claims["attrs"]
        }

        self.__logger.debug(
            'Built schemas: %s' %
            json.dumps(schemas))

        claim_defs_cache = {}
        claim_defs = {}
        for attr in claims["attrs"]:
            claim_def_cache_key = '%s::%s' % (
                    claims["attrs"][attr][0]["schema_seq_no"],
                    claims["attrs"][attr][0]["issuer_did"])

            if claim_def_cache_key not in claim_defs_cache:
                claim_defs_cache[claim_def_cache_key] = \
                    json.loads(await self.__orgbook.get_claim_def(
                        claims["attrs"][attr][0]["schema_seq_no"],
                        claims["attrs"][attr][0]["issuer_did"]
                    ))

            claim_defs[claims["attrs"][attr][0]['claim_uuid']] = \
                claim_defs_cache[claim_def_cache_key]

        self.__logger.debug(
            'Built claim_defs: %s' %
            json.dumps(claim_defs))

        self.__logger.debug("Creating proof ...")

        proof = await self.__orgbook.create_proof(
                json.dumps(self.__proof_request),
                json.dumps(schemas),
                json.dumps(claim_defs),
                requested_claims
            )

        self.__logger.debug(
            'Created proof: %s' %
            json.dumps(proof))

        return proof

    def ConstructProof(self):
        return eventloop.do(self.__ConstructProof())