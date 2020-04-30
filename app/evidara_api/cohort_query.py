# third party
import neo4j

# locals
from evidara_api.basic_query import BasicQuery
from evidara_api import models

COHORT_EDGE_PROPERTIES = ("adj_p_val", "p_val", "spearman_correlation")


class CohortQuery(BasicQuery):
    """Object for querying evidARA's internal SPOKE for data related to 
    observational cohorts"""

    def __init__(self, nodes, edges, query_options, n_results, caches=None):
        """Instantiates a new CohortQuery object"""
        super().__init__(nodes, edges, query_options, n_results)

    def get_cohort_str_repr(self, query_part, name, edge_type):
        """Returns string representation of node or edge for Cypher 
        querying. If an edge, returns a representation that maps to the
        cohort's respective edge type in SPOKE.

        Parameters
        ----------
        query_part (models.QNode or models.QEdge): a node or edge from a 
            QueryGraph
        name (str): alias for Cypher
        edge_type (str): edge type correpsonding to the cohort of 
        interest

        Returns
        -------
        node_repr (str): string representation of a query part, 
            e.g. "(c:Compound {chembl_id: 'CHEMBL1234'})"
        """
        if isinstance(query_part, models.QEdge):
            return f"[{name}:{edge_type}]"
        else:
            return self.get_n4j_str_repr(query_part, name)

    def get_returned_str_repr(self, edge_type):
        """Gets a string representation of the non-cohort nodes and
        edges that will be returned as a result
        
        Parameters
        edge_type (str): edge type correpsonding to the cohort of 
        interest

        Returns
        -------
        <unnamed> (str): string representation of non-cohort nodes
        and edges
        """
        query_parts = []
        return_edges = []
        for i, name in enumerate(self.query_names):
            if i % 2 == 0:
                query_parts.append(f"({name})")
            else:
                return_edges.append(f"type({name}1) <> '{edge_type}'")
                query_parts.append(f"[{name}1]")
        return "-".join(query_parts), return_edges

    def get_query_string(self):
        """Returns string representation of node or edge for Cypher 
        querying

        Parameters
        ----------
        query_part (models.QNode or models.QEdge): a node or edge from a 
            QueryGraph
        name (str): alias for Cypher

        Returns
        -------
        node_repr (str): string representation of a query part, 
            e.g. "(c:Compound {chembl_id: 'CHEMBL1234'})"
        """
        cohort_name = self.query_options["evidentiary"]
        self.query_names = "abcdefghijklmn"[: len(self.query_order)]
        query_parts = []
        self.query_mapping = {"edges": {}, "nodes": {}}
        for query_part, name in zip(self.query_order, self.query_names):
            query_parts.append(self.get_cohort_str_repr(query_part, name, cohort_name))
            if isinstance(query_part, models.QNode):
                self.query_mapping["nodes"][name] = query_part.node_id
            else:
                self.query_mapping["edges"][name] = query_part.edge_id
        cohort_string = "-".join(query_parts)
        return_string, return_edges = self.get_returned_str_repr(cohort_name)
        query_string = (
            f"match {cohort_string} "
            f"with {', '.join(list(self.query_names))} "
            f"match {return_string} "
            f"where {' and '.join(return_edges)} "
            f"return * limit {self.n_results}"
        )
        return query_string

    def make_evidara_result(self, n4j_result, record_number):
        """Constructs a reasoner-standard result from the result of a neo4j 
        query

        Parameters
        ----------
        n4j_result (neo4j.BoltStatementResult): result of a SPOKE Cypher
            query
        record_number (int): record index

        Returns
        -------
        <unnamed> (models.Result): reasoner-standard result that can be 
            returned to the user/ARS
        """
        # set up objects to collect results and query mappings
        result_nodes, result_edges = [], []
        knowledge_map = {"edges": {}, "nodes": {}}
        # iterate through results and add to result objects
        for name in self.query_names:
            if isinstance(n4j_result[name], neo4j.types.graph.Node):
                result_nodes.append(self.make_result_node(n4j_result[name]))
                knowledge_map["nodes"][
                    self.query_mapping["nodes"][name]
                ] = result_nodes[-1].id
            else:
                result_edges.append(
                    self.make_result_edge(n4j_result[name], n4j_result[name + "1"])
                )
                knowledge_map["edges"][
                    self.query_mapping["edges"][name]
                ] = result_edges[-1].id
        # score result, instiate result objects and return
        scores = self.get_result_score(result_nodes, result_edges)
        result_knowledge_graph = models.KnowledgeGraph(result_nodes, result_edges)
        return models.Result(
            id=record_number,
            result_graph=result_knowledge_graph,
            knowledge_map=knowledge_map,
            **scores,
        )

    def make_result_edge(self, cohort_edge, return_edge):
        """Instantiates a reasoner-standard Edge to return as part of a 
        KnowledgeGraph result

        Parameters
        ----------
        cohort_object, return_edge (abc.<neo4j edge type>): a 
        `relationship` object returned from a 
        neo4j.bolt.driver.session Cypher query

        Returns
        -------
        result_edge (models.Edge): reasoner-standard Edge object for
            inclusion as a part of a KnowledgeGraph result
        """
        result_edge = models.Edge(
            # TODO next two lines look up and include database per standards
            # TODO get reliable edge identifiers for `id` attribute
            # TODO get correlations score
            id=return_edge.id,  # this is meaningless id, but we use for viz
            source_id=return_edge.start_node["identifier"],
            target_id=return_edge.end_node["identifier"],
            type=return_edge.type,
        )
        result_edge.edge_attributes = [
            models.EdgeAttribute(type=k, value=v) for k, v in return_edge.items()
        ]
        result_edge.edge_attributes.extend(
            [
                models.EdgeAttribute(type=k, value=cohort_edge[k])
                for k in COHORT_EDGE_PROPERTIES
            ]
        )
        return result_edge
