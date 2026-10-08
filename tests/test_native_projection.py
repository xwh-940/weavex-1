"""Lossless incremental delivery in the existing persistent native dialogue."""

from copy import deepcopy

from weavex.native_session import NativeActorSession
from weavex.protocol import Observation


def receive(world, *, arrival=None, responsibilities=(), contexts=()):
    return Observation(
        "receive_input",
        True,
        value={
            "arrival": arrival
            or {"act_id": "q", "kind": "question", "payload": {"raw_question": "Q"}},
            "answer_responsibilities": list(responsibilities),
            "visible_acts": [],
            "local_world": world,
            "external_contexts": list(contexts),
        },
    )


def test_full_first_delivery_then_only_new_material_and_changed_observation_origins():
    session = NativeActorSession()
    o = {"object_id": "o", "content": "exact content"}
    r = {
        "reference_id": "r",
        "source_object_id": "o",
        "target": {"kind": "object", "id": "o"},
        "meaning": "relation",
    }
    boundary = {
        "observation_id": "ob",
        "address": {"kind": "object", "id": "o"},
        "direction": "outgoing",
        "established_from_question_ids": ["q"],
    }
    grounding = {"grounding_id": "g", "inputs": [{"kind": "observation", "id": "ob"}]}
    world = {
        "domain": "semantic",
        "objects": [o],
        "references": [r],
        "observed_neighborhoods": [boundary],
        "groundings": [grounding],
    }
    q = {
        "act_id": "q",
        "kind": "question",
        "payload": {"raw_question": "Q"},
        "response_frontier": {"gap": "no_public_answer"},
    }
    original = receive(
        world,
        responsibilities=[q],
        contexts=[
            {
                "context_id": "c",
                "guaranteed_shared_context": {"origin_question": "shared"},
            }
        ],
    )
    saved = deepcopy(original)
    first = session.project_observation(original)
    assert first.value["local_world"]["objects"] == [o]
    assert first.value["local_world"]["projection"] == "initial"
    assert first.value["answer_responsibilities"][0]["payload"] == q["payload"]
    assert original == saved
    world = deepcopy(world)
    world["objects"].append({"object_id": "new", "content": "new content"})
    world["observed_neighborhoods"][0]["established_from_question_ids"].append("q2")
    second = session.project_observation(
        receive(
            world, responsibilities=[q], contexts=original.value["external_contexts"]
        )
    )
    assert second.value["local_world"]["objects"] == [
        {"object_id": "new", "content": "new content"}
    ]
    assert second.value["local_world"]["references"] == []
    assert second.value["local_world"]["groundings"] == []
    assert second.value["local_world"]["observed_neighborhoods"] == [
        {"observation_id": "ob", "question_origins_added": ["q2"]}
    ]
    assert second.value["external_contexts"] == []
    responsibility = second.value["answer_responsibilities"][0]
    assert "payload" not in responsibility and responsibility["previously_projected"]
    assert responsibility["response_frontier"] == q["response_frontier"]
    assert (
        session.project_observation(receive(world)).value["local_world"][
            "observed_neighborhoods"
        ]
        == []
    )


def test_successful_world_effects_advance_delivery_but_failed_effects_do_not():
    session = NativeActorSession()
    world = {"domain": "semantic", "objects": [], "references": []}
    session.project_observation(receive(world))
    created = {"object_id": "created", "content": "created once"}
    session.project_observation(Observation("world_add_object", True, created))
    rejected = {"object_id": "rejected", "content": "not delivered"}
    session.project_observation(Observation("world_add_object", False, rejected))
    acquired = {"object_id": "acquired", "content": "acquired once"}
    ref = {
        "reference_id": "r",
        "source_object_id": "created",
        "target": {"kind": "object", "id": "acquired"},
        "meaning": "",
    }
    boundary = {
        "observation_id": "ob",
        "address": {"kind": "object", "id": "created"},
        "direction": "outgoing",
        "established_from_question_ids": ["q"],
    }
    effect = Observation(
        "world_references_from",
        True,
        {
            "references": [ref],
            "observation": boundary,
            "local_world_change": {
                "objects_added": [acquired],
                "references_added": [ref],
            },
        },
    )
    assert session.project_observation(effect) == effect
    second = session.project_observation(
        receive(
            {
                **world,
                "objects": [created, acquired, rejected],
                "references": [ref],
                "observed_neighborhoods": [boundary],
            }
        )
    )
    assert second.value["local_world"]["objects"] == [rejected]
    assert second.value["local_world"]["references"] == []
    assert second.value["local_world"]["observed_neighborhoods"] == []


def test_delta_arrival_delivers_new_material_once_and_keeps_small_current_state():
    session = NativeActorSession()
    world = {"domain": "semantic", "objects": [], "references": []}
    session.project_observation(receive(world))
    obj = {"object_id": "new", "content": "arrived"}
    arrival = {
        "kind": "semantic_neighborhood_change",
        "delta_id": "d",
        "local_world_change": {"objects_added": [obj], "references_added": []},
    }
    result = session.project_observation(
        receive({**world, "objects": [obj]}, arrival=arrival)
    )
    assert result.value["arrival"]["local_world_change"]["objects_added"] == [obj]
    assert result.value["local_world"]["objects"] == []
    session = NativeActorSession()
    context = {
        "domain": "coordination",
        "actors": [{"actor_id": "a", "capabilities": []}],
        "context": {"formed": False},
        "candidates": [{"actor_id": "candidate"}],
        "open_question_responsibilities": [{"question_id": "q"}],
    }
    session.project_observation(receive(context))
    updated = {
        **context,
        "context": {"formed": True},
        "candidates": [],
        "open_question_responsibilities": [],
    }
    result = session.project_observation(receive(updated))
    assert result.value["local_world"]["actors"] == []
    assert result.value["local_world"]["context"] == {"formed": True}
    assert result.value["local_world"]["candidates"] == []
    assert result.value["local_world"]["open_question_responsibilities"] == []
    assert (
        NativeActorSession()
        .project_observation(receive(updated))
        .value["local_world"]["actors"]
        == context["actors"]
    )
