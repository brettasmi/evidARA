from collections import defaultdict

from werkzeug.exceptions import BadRequest

from improving_agent.exceptions import (
    MissingComponentError,
    UnsupportedTypeError
)
from improving_agent.models import QEdge
from improving_agent.src.spoke_biolink_constants import PREDICATES


def _deserialize_qedge(qedge_id, qedge):
    try:
        subject = qedge['subject']
        object_ = qedge['object']
        predicate = qedge.get('predicate')
        relation = qedge.get('relation')
        qedge = QEdge(predicate, relation, subject, object_)
        setattr(qedge, 'qedge_id', qedge_id)
    except (KeyError, TypeError):
        raise BadRequest(f'Could not deserialize query edge {qedge_id}')

    return qedge


def _get_objects_maps(subj_qnode):
    if not subj_qnode.category:
        return list(PREDICATES.values())

    objects_maps = []
    for category in subj_qnode.category:
        objects_map = PREDICATES.get(category)
        if objects_map:
            objects_maps.append(objects_map)

    if not objects_maps:
        raise UnsupportedTypeError(f'Could not find any supported predicates for subject category: {subj_qnode.category}')

    return objects_maps


def _get_potential_predicate_maps(subj_qnode, obj_qnode):
    objects_maps = _get_objects_maps(subj_qnode)
    potential_predicates_map = defaultdict(list)
    if not obj_qnode.category:
        for objects_map in objects_maps:
            for predicate, spoke_edges in objects_map.items():
                potential_predicates_map[predicate].extend(spoke_edges)
        return potential_predicates_map

    for category in obj_qnode.category:
        for objects_map in objects_maps:
            predicates_map = objects_map.get(category)
            if not predicates_map:
                continue
            for predicate, spoke_edges in predicates_map.items():
                potential_predicates_map[predicate].extend(spoke_edges)

    if not potential_predicates_map:
        raise UnsupportedTypeError(
            'Could not find any supported predicates for subject category: '
            f'{subj_qnode.category} and object category: {obj_qnode.category}'
        )
    print(potential_predicates_map)
    return potential_predicates_map


def _get_subject_object_qnodes(query_graph, qedge):
    subject_node = query_graph.nodes.get(qedge.subject)
    object_node = query_graph.nodes.get(qedge.object)
    if not subject_node or not object_node:
        raise MissingComponentError(f'Subject or object missing for query edge {qedge.qedge_id}')

    return subject_node, object_node


def _assign_spoke_edge_types(qedge, subj_qnode, obj_qnode, query_graph):
    spoke_edge_types = []
    if qedge.predicate:
        # TODO: this may not perform well, evaluate it against the quick
        # lookup and disambugation implemented up to commit f17f12a261ce1b6f80ae47b86a6c704639566444
        valid_predicates_maps = _get_potential_predicate_maps(subj_qnode, obj_qnode)
        for predicate in qedge.predicate:
            spoke_edge_mappings = valid_predicates_maps.get(predicate)
            if not spoke_edge_mappings:
                raise UnsupportedTypeError(f'imProving Agent does not currently accept predicates of type {predicate}')
            spoke_edge_types.extend(spoke_edge_mappings)

    setattr(qedge, 'spoke_edge_types', set(spoke_edge_types))
    return qedge


def validate_normalize_qedges(query_graph):
    qedges = {}
    for qedge_id, qedge in query_graph.edges.items():
        qedge = _deserialize_qedge(qedge_id, qedge)
        subj_qnode, obj_qnode = _get_subject_object_qnodes(query_graph, qedge)
        qedge = _assign_spoke_edge_types(qedge, subj_qnode, obj_qnode, query_graph)
        qedges[qedge_id] = qedge

    return qedges
