#!/usr/bin/env python
# These functions should contain the core logic of evidARA-SPOKE
# interactions

# locals
from evidara_api.__main__ import get_db
from evidara_api.basic_query import BasicQuery
from evidara_api.cohort_query import CohortQuery
from evidara_api.biggim import BigGimRequester
from evidara_api import models
from evidara_api.util import get_evidara_logger

logger = get_evidara_logger(__name__)

# set up globabl caches for knowledge providers
# depending on the future of translator, this might be better handled
# by an explicit caching system like `requests-cache`
kp_caches = {"big_gim": BigGimRequester()}


def process_query(query):
    """Maps query nodes to SPOKE equivalents
    
    Parameters
    ----------
    query (models.Query): user/ARS query from the query_controller
    handler
    
    Returns
    -------
    res (dict or str): one key (`results`) mapped to a list of 
        reasoner-standard evidara.models.Result objects; alternatively 
        returns str message on error
    """
    # manually unpack query, checking for model compliance along the way
    # raise and return 400 on failure to instantiate
    try:
        logger.info("Got query...")
        # hopefully these recursively unpack in the future upon creation
        # of the Query object, but if not, we can also consider the
        # .from_dict() method on these objects instead of ** syntax
        query_message = models.Message(**query.query_message)
        query_graph = models.QueryGraph(**query_message.query_graph)
        nodes = [models.QNode(**node) for node in query_graph.nodes]
        edges = [models.QEdge(**edge) for edge in query_graph.edges]
    except TypeError as e:
        return f"Bad Request with keyword {str(e).split()[-1]}", 400
    # set empty query options for non-error lookups
    query_options = query_message.query_options if query_message.query_options else {}
    queriers, results = [], []
    if "evidentiary" in query_options:
        queriers.append(
            CohortQuery(nodes, edges, query_message.query_options, 200, kp_caches)
        )
    queriers.append(
        BasicQuery(nodes, edges, query_message.query_options, 200, kp_caches)
    )
    # now query SPOKE
    with get_db() as session:
        for querier in queriers:
            res, query_order = querier.spoke_query(session)
            if isinstance(res, str):
                return res, 400
            results.extend(res)
            if len(results) > 200:
                break

        # check BigGIM, currently here, but a better `process_results`
        # function should be created in the future
        results = kp_caches["big_gim"].annotate_edges_with_biggim(
            session, query_order, results, query_options.get("psev-context"),
        )

    knowledge = {"results": sorted(results, key=lambda x: x.score, reverse=True)[:20]}
    return knowledge
