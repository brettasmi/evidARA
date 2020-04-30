# std library

from collections import Counter

# third party
import neo4j

# locals
from evidara_api import models
from evidara_api.exceptions import MissingComponentError
from evidara_api.psev import get_psev_weights
from evidara_api.spoke_constants import (
    BIOLINK_SPOKE_NODE_MAPPINGS,
    SPOKE_BIOLINK_NODE_MAPPINGS,
)

# logger
from evidara_api.util import get_evidara_logger

logger = get_evidara_logger(__name__)


class BasicQuery:
    """A class for making basic queries to the SPOKE neo4j database"""

    def __init__(self, nodes, edges, query_options, n_results, caches=None):
        """Instantiates a new BasicQuery object"""
        self.nodes = nodes
        self.edges = edges
        self.query_options = query_options
        self.n_results = n_results if n_results else 200
        self.result_nodes = {}
        self.caches = caches

    def query_setup(self):
        """"""
        # get nodes as dict for later lookup by identifier
        self.query_nodes_map = self.make_node_dictionary(self.nodes)
        if isinstance(self.query_nodes_map, str):
            raise NotImplementedError(f"{self.query_nodes_map}")
        self.make_query_order()

    def get_n4j_param_str(self, parameters):
        """Returns a string properly formatted for neo4j parameter-based 
        searching
        
        Parameters
        ----------
        parameters (dict of str -> int|str): parameter mappings to 
        convert to string suitable for Cypher querying

        Returns
        -------
        <unnamed> (str): formatted string for parameter search
        """
        param_string = ", ".join(
            [
                f"{k}:'{v}'" if isinstance(v, str) else f"{k}:{v}"
                for k, v in parameters.items()
            ]
        )
        return "{" + param_string + "}"

    def get_n4j_str_repr(self, query_part, name):
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
        # not supporting specific edge types until mapped to biolink
        if isinstance(query_part, models.QEdge):
            return f"[{name}]"

        # start constructing the string, then add optional features
        node_repr = f"({name}"
        # add a label if we can, then add parameters if possible
        spoke_label_id_config = BIOLINK_SPOKE_NODE_MAPPINGS[query_part.type]
        if spoke_label_id_config[0]:
            spoke_label = spoke_label_id_config[0]
            node_repr += f":{spoke_label} "
            # add a parameter if we can
            if query_part.curie:
                # handle different configs for different node identifiers
                if spoke_label_id_config[1] == "split":
                    curie = query_part.curie.split(":")
                    if len(curie) > 2:
                        curie = ":".join(curie[1:])
                    else:
                        curie = curie[1]
                else:
                    curie = query_part.curie
                try:
                    # TODO make sure this isn't a float
                    # currently none in spoke, but this could easily happen
                    curie = int(curie)
                except ValueError:
                    pass
                parameter_string = self.get_n4j_param_str({"identifier": curie})
                node_repr += f"{parameter_string}"
        node_repr += ")"
        return node_repr

    def make_node_dictionary(self, nodes):
        """Validates that SPOKE contains requested node type and creates a 
        dictionary from a list of evidara.models.nodes

        Parameters
        ----------
        nodes (list of evidara.models.QNode): array of nodes from a 
            user/ARS QueryGraph

        Returns
        -------
        node_d (dict or str) OR errors(str): dictionary of 
            str -> evidara.models.QNode on success; string of incompatible
            node types on failure
        """
        # here we just validate that we can actually look up nodes of a
        # requested type
        node_d = {}
        errors = []
        for node in nodes:
            if node.type:
                try:
                    node.spoke_label = BIOLINK_SPOKE_NODE_MAPPINGS[node.type][0]
                except KeyError:
                    errors.append(
                        f"Node type {node.type} not (yet) supported by evidARA"
                    )
            node_d[node.node_id] = node
        if len(errors):
            return ", ".join(errors)
        return node_d

    def make_query_order(self):
        """Constructs a list of QNodes and QEdges in the order in which
        they should be sent to neo4j for querying
        """
        # process edges to find terminal nodes so the query can be ordered
        if len(self.edges) > 1:
            node_appearances = []
            _ = [
                node_appearances.extend([e.source_id, e.target_id]) for e in self.edges
            ]
            node_count = Counter(node_appearances)
            terminal_nodes = [node for node in node_count if node_count[node] == 1]
        else:
            terminal_nodes = list(self.query_nodes_map.keys())

        # start query order with either of the terminal nodes
        self.query_order = [self.query_nodes_map[terminal_nodes[0]]]
        target_query_length = len(self.nodes) + len(self.edges)

        # create copy of edges that can be destroyed
        edges_copy = self.edges.copy()
        while len(self.query_order) < target_query_length:
            found_flag = False
            if isinstance(self.query_order[-1], models.QNode):
                for i, edge in enumerate(edges_copy):
                    if self.query_order[-1].node_id in (edge.source_id, edge.target_id):
                        found_flag = True
                        break
                if found_flag:
                    self.query_order.append(edges_copy.pop(i))
                else:
                    raise MissingComponentError(
                        "Couldn't find edge corresponding to "
                        f"{self.query_order[-1].node_id}"
                    )
            else:
                next_node = [
                    self.query_order[-1].source_id,
                    self.query_order[-1].target_id,
                ]
                next_node.remove(self.query_order[-2].node_id)
                if len(next_node) == 1:
                    self.query_order.append(self.query_nodes_map[next_node[0]])
                else:
                    raise MissingComponentError(f"Missing one of {next_node}")

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
                result_edges.append(self.make_result_edge(n4j_result[name]))
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

    def get_result_score(self, nodes, edges):
        """Returns a score based on psev weights, cohort edge correlations,
            both, or none

        Parameters
        ----------
        nodes (list of models.Node): reasoner-standard Nodes that have 
            a NodeAttribute for psev_weight. This is currently implemented
            to only check the final element in the list of NodeAttributes 
            for each Node
        edges (list of models.Edge): reasoner-standard Edges that have 
            an EdgeAttribute for cohort_correlation. This is currently 
            implemented to only check the final element in the list of 
            EdgeAttributes for each Edge
        
        Returns
        -------
        scores (dict): mapping of score (str) -> float and 
            score name (str) -> str
        

        NOTE: currently for linear queries, all knowledge graphs are of the
            length, but dividing the sum score by `n` may make sense in the 
            future
        """
        scores = {}
        if ("psev-context" in self.query_options) & (
            "evidentiary" in self.query_options
        ):
            scores["score"] = (
                sum(
                    [
                        node.node_attributes[-1].value
                        for node in nodes
                        if node.node_attributes[-1].type == "psev_weight"
                    ]
                )
                * 10000
            )
            scores["score"] += (
                sum(
                    [
                        edge.edge_attributes[-1].value
                        for edge in edges
                        if edge.edge_attributes[-1].type == "spearman_correlation"
                    ]
                )
                / 20
            )
            scores["score_name"] = "evidara-combined-psev-cohort"
        elif "psev-context" in self.query_options:
            scores["score"] = (
                sum(
                    [
                        node.node_attributes[-1].value
                        for node in nodes
                        if node.node_attributes[-1].type == "psev_weight"
                    ]
                )
                * 10000
            )
            scores["score_name"] = "evidara-psev"
        elif "evidentiary" in self.query_options:
            scores["score"] = (
                sum(
                    [
                        edge.edge_attributes[-1].value
                        for edge in edges
                        if edge.edge_attributes[-1].type == "spearman_correlation"
                    ]
                )
                / 20
            )
            scores["score_name"] = "evidara-cohort"
        else:
            scores["score"] = 0
            scores["score_name"] = None
        return scores

    def make_result_node(self, n4j_object):
        """Instantiates a reasoner-standard Node to return as part of a 
        KnowledgeGraph result

        Parameters
        ----------
        n4j_object (neo4j.types.graph.Node): a `Node` object returned from a
            neo4j.bolt.driver.session Cypher query

        Returns
        -------
        result_node (models.Edge): a reasoner-standard `Edge` object for
            inclusion as part of a KnowledgeGraph result

        """
        result_node = models.Node(
            id=n4j_object[
                "identifier"
            ],  # TODO look up and include database for standards
            name=n4j_object.get("name"),
            type=[
                SPOKE_BIOLINK_NODE_MAPPINGS[label] for label in list(n4j_object.labels)
            ],
            description=n4j_object.get("description"),
        )
        result_node.node_attributes = [
            models.NodeAttribute(type=k, value=v) for k, v in n4j_object.items()
        ]
        if "psev-context" in self.query_options:
            try:
                result_node.node_attributes.append(
                    models.NodeAttribute(
                        type="psev_weight",
                        value=get_psev_weights(
                            node_identifier=n4j_object["identifier"],
                            disease_identifier=self.query_options["psev-context"],
                        ),
                    )
                )
            except IndexError:  # TODO decide if this should just not happen, i.e. is it really a 0?
                result_node.node_attributes.append(
                    models.NodeAttribute(type="psev_weight", value=0)
                )
        return result_node

    def make_result_edge(self, n4j_object):
        """Instantiates a reasoner-standard Edge to return as part of a 
        KnowledgeGraph result

        Parameters
        ----------
        n4j_object (abc.<neo4j edge type>): a `relationship` object returned
            from a neo4j.bolt.driver.session Cypher query

        Returns
        -------
        result_edge (models.Edge): reasoner-standard Edge object for
            inclusion as a part of a KnowledgeGraph result
        """
        result_edge = models.Edge(
            # TODO next two lines look up and include database per standards
            # TODO get reliable edge identifiers for `id` attribute
            # TODO get correlations score
            id=n4j_object.id,  # this is meaningless id, but we use for viz
            source_id=n4j_object.start_node["identifier"],
            target_id=n4j_object.end_node["identifier"],
            type=n4j_object.type,
        )
        result_edge.edge_attributes = [
            models.EdgeAttribute(type=k, value=v) for k, v in n4j_object.items()
        ]
        return result_edge

    def get_query_string(self):
        """Returns a Cypher string to pass to SPOKE"""
        # spoke diameter is <7 but consider enforcing max query length anyway
        # possibly enforce a max on the query too
        self.query_names = "abcdefghijklmn"[: len(self.query_order)]
        query_parts = []
        self.query_mapping = {"edges": {}, "nodes": {}}
        for query_part, name in zip(self.query_order, self.query_names):
            query_parts.append(self.get_n4j_str_repr(query_part, name))
            if isinstance(query_part, models.QNode):
                self.query_mapping["nodes"][name] = query_part.node_id
            else:
                self.query_mapping["edges"][name] = query_part.edge_id
        query_string = "-".join(query_parts)
        return f"match p = {query_string} " f"return * limit {self.n_results}"

    def spoke_query(self, session):
        """Returns the SPOKE node label equivalent to `node_type`

        Parameters
        ----------
        session (neo4j.driver.session): active neo4j session

        Returns
        -------
        results (dict or str): one key (`results`) mapped to a list of 
            reasoner-standard evidara.models.Result objects; alternatively 
            returns str message on error
        """
        try:
            self.query_setup()
        except (NotImplementedError, MissingComponentError) as e:
            return e, None
        query_string = self.get_query_string()
        r = session.run(query_string)

        # create the results, then sort on score
        results = [
            self.make_evidara_result(record, i) for i, record in enumerate(r.records())
        ]

        return results, self.query_order
