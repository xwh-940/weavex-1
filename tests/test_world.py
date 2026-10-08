from __future__ import annotations

import unittest
from dataclasses import fields

from weavex import (
    ObjectAddress,
    ReferenceAddress,
    SemanticObject,
    SemanticReference,
    SemanticWorld,
    StructuralError,
)


class SemanticWorldPrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = SemanticWorld()
        self.source = self.world.add_object("source meaning", object_id="object:source")
        self.target = self.world.add_object("target meaning", object_id="object:target")

    def test_semantic_values_contain_no_operational_metadata(self) -> None:
        self.assertEqual(
            tuple(value.name for value in fields(SemanticObject)),
            ("object_id", "content"),
        )
        self.assertEqual(
            tuple(value.name for value in fields(SemanticReference)),
            ("reference_id", "source_object_id", "target_address", "meaning"),
        )

    def test_object_can_reference_an_object(self) -> None:
        reference = self.world.add_reference(
            self.source.object_id,
            ObjectAddress(self.target.object_id),
            meaning="target supplies the source's decisive premise",
            reference_id="reference:object-target",
        )

        self.assertEqual(
            reference.meaning,
            "target supplies the source's decisive premise",
        )
        self.assertEqual(
            self.world.references_from(self.source.object_id),
            (reference,),
        )
        self.assertEqual(
            self.world.references_to(ObjectAddress(self.target.object_id)),
            (reference,),
        )

    def test_reference_is_addressable_but_only_object_can_be_source(self) -> None:
        first = self.world.add_reference(
            self.source.object_id,
            ObjectAddress(self.target.object_id),
            reference_id="reference:first",
        )
        self.assertEqual(first.meaning, "")
        commentary = self.world.add_object(
            "meaning about the first reference",
            object_id="object:commentary",
        )
        second = self.world.add_reference(
            commentary.object_id,
            ReferenceAddress(first.reference_id),
            reference_id="reference:second",
        )

        self.assertEqual(
            self.world.references_to(ReferenceAddress(first.reference_id)),
            (second,),
        )
        with self.assertRaises(StructuralError):
            self.world.add_reference(  # type: ignore[arg-type]
                ReferenceAddress(first.reference_id),
                ObjectAddress(self.target.object_id),
            )

    def test_reference_requires_existing_source_and_target(self) -> None:
        with self.assertRaises(StructuralError):
            self.world.add_reference(
                "object:missing",
                ObjectAddress(self.target.object_id),
            )
        with self.assertRaises(StructuralError):
            self.world.add_reference(
                self.source.object_id,
                ReferenceAddress("reference:missing"),
            )

    def test_reference_meaning_is_open_text_but_structurally_a_string(self) -> None:
        with self.assertRaises(StructuralError):
            self.world.add_reference(
                self.source.object_id,
                ObjectAddress(self.target.object_id),
                meaning=17,  # type: ignore[arg-type]
            )

    def test_snapshot_does_not_follow_later_world_changes(self) -> None:
        snapshot = self.world.snapshot()
        later = self.world.add_object("later meaning", object_id="object:later")

        self.assertNotIn(later.object_id, snapshot.objects)
        self.assertIn(later.object_id, self.world.snapshot().objects)
        with self.assertRaises(TypeError):
            snapshot.objects["object:illegal"] = later  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
