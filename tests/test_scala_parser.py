"""Tests for Scala 3 parsing with significant indentation."""

import pytest
from jcodemunch_mcp.parser import parse_file, LANGUAGE_REGISTRY


SCALA3_SOURCE = '''
package com.example

import scala.compiletime.uninitialized

/** Feed repository trait. */
@Repository
trait FeedRepository extends JpaRepository[Feed, UUID]:
  /** Find by link. */
  def findByLink(link: String): Option[Feed]
  def findByTitle(title: String): List[Feed]

/** Feed service. */
@Service
class FeedService(repo: FeedRepository):
  val batchSize: Int = 100
  var mutableCount: Int = 0

  def fetchAll(): List[Feed] =
    repo.findAll().asScala.toList

type MyAlias = List[String]

object Constants:
  val MaxRetries = 3
  var Counter: Int = 0

enum Status:
  case Active, Inactive

  def isActive: Boolean = this == Active
'''


def test_parse_scala3():
    """Test Scala 3 significant-indentation parsing."""
    symbols = parse_file(SCALA3_SOURCE, "test.scala", "scala")

    # Trait
    trait = next((s for s in symbols if s.name == "FeedRepository"), None)
    assert trait is not None
    assert trait.kind == "type"

    # Abstract method in trait (function_declaration)
    abstract = next((s for s in symbols if s.name == "findByLink"), None)
    assert abstract is not None
    assert abstract.kind == "method"
    assert "Option[Feed]" in abstract.signature

    # Second abstract method
    abstract2 = next((s for s in symbols if s.name == "findByTitle"), None)
    assert abstract2 is not None
    assert abstract2.kind == "method"

    # Class
    cls = next((s for s in symbols if s.name == "FeedService"), None)
    assert cls is not None
    assert cls.kind == "class"

    # Concrete method in class (function_definition)
    concrete = next((s for s in symbols if s.name == "fetchAll"), None)
    assert concrete is not None
    assert concrete.kind == "method"

    # val field
    val_field = next((s for s in symbols if s.name == "batchSize"), None)
    assert val_field is not None
    assert val_field.kind == "constant"

    # var field
    var_field = next((s for s in symbols if s.name == "mutableCount"), None)
    assert var_field is not None
    assert var_field.kind == "constant"

    # type alias
    alias = next((s for s in symbols if s.name == "MyAlias"), None)
    assert alias is not None
    assert alias.kind == "type"

    # object
    obj = next((s for s in symbols if s.name == "Constants"), None)
    assert obj is not None
    assert obj.kind == "class"

    # val in object
    obj_val = next((s for s in symbols if s.name == "MaxRetries"), None)
    assert obj_val is not None
    assert obj_val.kind == "constant"

    # var in object
    obj_var = next((s for s in symbols if s.name == "Counter"), None)
    assert obj_var is not None
    assert obj_var.kind == "constant"

    # enum
    enum_type = next((s for s in symbols if s.name == "Status"), None)
    assert enum_type is not None
    assert enum_type.kind == "type"

    # method inside enum
    enum_method = next((s for s in symbols if s.name == "isActive"), None)
    assert enum_method is not None
    assert enum_method.kind == "method"


def test_scala_spec_registered():
    """Verify the Scala language spec is registered."""
    assert "scala" in LANGUAGE_REGISTRY
    spec = LANGUAGE_REGISTRY["scala"]
    assert spec.ts_language == "scala"
    assert "function_declaration" in spec.symbol_node_types
    assert "val_definition" in spec.symbol_node_types
    assert "var_definition" in spec.symbol_node_types
    assert "type_definition" in spec.symbol_node_types
