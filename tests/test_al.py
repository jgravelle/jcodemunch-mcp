"""Tests for AL (Business Central) symbol extraction."""

import pytest
from pathlib import Path

from src.jcodemunch_mcp.parser.extractor import parse_file
from src.jcodemunch_mcp.parser.languages import get_language_for_path, LANGUAGE_EXTENSIONS


FIXTURE = Path(__file__).parent / "fixtures" / "al" / "sample.al"


def _load():
    return FIXTURE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Extension / language detection
# ---------------------------------------------------------------------------

def test_al_extension_detected():
    assert get_language_for_path("app/MyCodeunit.al") == "al"


def test_al_extension_in_registry():
    assert ".al" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".al"] == "al"


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

def _symbols():
    return parse_file(_load(), "app/Sample.al", "al")


def test_al_returns_symbols():
    syms = _symbols()
    assert len(syms) >= 40


# --- Top-level objects ---

def test_al_table_extracted():
    syms = _symbols()
    tables = [s for s in syms if s.name == "My Table" and s.kind == "class"]
    assert len(tables) == 1


def test_al_codeunit_extracted():
    syms = _symbols()
    codeunits = [s for s in syms if s.name == "My Codeunit" and s.kind == "class"]
    assert len(codeunits) == 1


def test_al_enum_extracted():
    syms = _symbols()
    enums = [s for s in syms if s.name == "My Enum" and s.kind == "type"]
    assert len(enums) == 1


def test_al_interface_extracted():
    syms = _symbols()
    ifaces = [s for s in syms if s.name == "IMyInterface" and s.kind == "type"]
    assert len(ifaces) == 1


def test_al_report_extracted():
    syms = _symbols()
    reports = [s for s in syms if s.name == "My Report" and s.kind == "class"]
    assert len(reports) == 1


def test_al_query_extracted():
    syms = _symbols()
    queries = [s for s in syms if s.name == "My Query" and s.kind == "class"]
    assert len(queries) == 1


def test_al_xmlport_extracted():
    syms = _symbols()
    xmlports = [s for s in syms if s.name == "My XMLport" and s.kind == "class"]
    assert len(xmlports) == 1


def test_al_extension_objects():
    syms = _symbols()
    ext_names = {"My Page Ext", "My Table Ext", "My Enum Ext"}
    ext_syms = [s for s in syms if s.name in ext_names]
    assert len(ext_syms) == 3
    assert all(s.kind == "class" for s in ext_syms)


def test_al_page_extracted():
    syms = _symbols()
    pages = [s for s in syms if s.name == "My Page" and s.kind == "class"]
    assert len(pages) == 1


def test_al_controladdin_extracted():
    syms = _symbols()
    addins = [s for s in syms if s.name == "MyAddIn" and s.kind == "class"]
    assert len(addins) == 1


# --- Procedures ---

def test_al_procedure_extracted():
    syms = _symbols()
    procs = [s for s in syms if s.name == "MyMethod" and s.kind == "method"]
    assert len(procs) == 1
    assert "Param1: Integer" in procs[0].signature
    assert ": Boolean" in procs[0].signature


def test_al_local_procedure_extracted():
    syms = _symbols()
    procs = [s for s in syms if s.name == "PrivateHelper" and s.kind == "method"]
    assert len(procs) == 1
    assert "local" in procs[0].signature


def test_al_internal_procedure_extracted():
    syms = _symbols()
    procs = [s for s in syms if s.name == "DocumentedProc" and s.kind == "method"]
    assert len(procs) == 1
    assert "internal" in procs[0].signature


def test_al_interface_procedures():
    syms = _symbols()
    procs = [s for s in syms if s.name in ("DoWork", "GetValue") and s.kind == "method"]
    assert len(procs) == 2


# --- Triggers ---

def test_al_trigger_extracted():
    syms = _symbols()
    triggers = [s for s in syms if s.name == "OnRun" and s.kind == "method"]
    assert len(triggers) == 1
    assert "trigger" in triggers[0].signature


# --- Fields ---

def test_al_field_extracted():
    syms = _symbols()
    fields = [s for s in syms if s.kind == "constant" and "field(" in s.signature and ";" in s.signature and s.signature.count(";") >= 2]
    field_names = {s.name for s in fields}
    assert "No." in field_names
    assert "Name" in field_names


def test_al_field_in_extension():
    syms = _symbols()
    fields = [s for s in syms if s.name == "Custom Field" and s.kind == "constant" and "field(" in s.signature and "50100" in s.signature]
    assert len(fields) >= 1


# --- Enum Values ---

def test_al_enum_value_extracted():
    syms = _symbols()
    values = [s for s in syms if s.kind == "constant" and "value(" in s.signature]
    value_names = {s.name for s in values}
    assert "None" in value_names
    assert "Option1" in value_names


def test_al_enum_value_in_extension():
    syms = _symbols()
    values = [s for s in syms if s.name == "NewOption" and s.kind == "constant" and "value(" in s.signature]
    assert len(values) == 1


def test_al_enum_value_qualified_name():
    syms = _symbols()
    val = next((s for s in syms if s.name == "Option1" and "value(" in s.signature), None)
    assert val is not None
    assert val.qualified_name == "My Enum.Option1"


def test_al_enum_value_signature():
    syms = _symbols()
    val = next((s for s in syms if s.name == "Option1" and "value(" in s.signature), None)
    assert val is not None
    assert val.signature == "value(1; Option1)"


def test_al_enum_value_parent():
    syms = _symbols()
    val = next((s for s in syms if s.name == "Option1" and "value(" in s.signature), None)
    assert val is not None
    assert val.parent is not None


# --- Page Actions ---

def test_al_page_action_extracted():
    syms = _symbols()
    actions = [s for s in syms if s.kind == "function" and "action(" in s.signature]
    assert len(actions) >= 2


def test_al_page_action_qualified_name():
    syms = _symbols()
    act = next((s for s in syms if s.name == "PostDocument" and s.kind == "function"), None)
    assert act is not None
    assert act.qualified_name == "My Page.PostDocument"


def test_al_page_action_quoted_name():
    syms = _symbols()
    act = next((s for s in syms if s.name == "Run Report" and s.kind == "function"), None)
    assert act is not None


# --- Keys ---

def test_al_key_extracted():
    syms = _symbols()
    keys = [s for s in syms if "key(" in s.signature and s.kind == "constant"]
    assert len(keys) >= 2


def test_al_key_qualified_name():
    syms = _symbols()
    key = next((s for s in syms if s.name == "PK" and "key(" in s.signature), None)
    assert key is not None
    assert key.qualified_name == "My Table.PK"


def test_al_key_signature_includes_columns():
    syms = _symbols()
    key = next((s for s in syms if s.name == "PK" and "key(" in s.signature), None)
    assert key is not None
    assert '"No."' in key.signature


# --- Report/Query Columns ---

def test_al_report_column_extracted():
    syms = _symbols()
    cols = [s for s in syms if s.name == "No" and "column(" in s.signature
            and s.parent is not None and "My Report" in s.parent]
    assert len(cols) == 1


def test_al_query_column_extracted():
    syms = _symbols()
    cols = [s for s in syms if s.name == "No" and "column(" in s.signature
            and s.parent is not None and "My Query" in s.parent]
    assert len(cols) == 1


def test_al_column_signature():
    syms = _symbols()
    col = next((s for s in syms if s.name == "No" and "column(" in s.signature), None)
    assert col is not None
    assert '"No."' in col.signature


# --- FieldGroups ---

def test_al_fieldgroup_extracted():
    syms = _symbols()
    fgs = [s for s in syms if "fieldgroup(" in s.signature and s.kind == "constant"]
    assert len(fgs) >= 1


def test_al_fieldgroup_signature():
    syms = _symbols()
    fg = next((s for s in syms if s.name == "DropDown" and "fieldgroup(" in s.signature), None)
    assert fg is not None
    assert '"No."' in fg.signature or "Name" in fg.signature


def test_al_fieldgroup_qualified_name():
    syms = _symbols()
    fg = next((s for s in syms if s.name == "DropDown" and "fieldgroup(" in s.signature), None)
    assert fg is not None
    assert fg.qualified_name == "My Table.DropDown"


# --- DataItems ---

def test_al_report_dataitem_extracted():
    syms = _symbols()
    dis = [s for s in syms if s.name == "MyTable" and "dataitem(" in s.signature
           and s.parent is not None and "My Report" in s.parent]
    assert len(dis) == 1


def test_al_query_dataitem_extracted():
    syms = _symbols()
    dis = [s for s in syms if s.name == "MyTable" and "dataitem(" in s.signature
           and s.parent is not None and "My Query" in s.parent]
    assert len(dis) == 1


def test_al_dataitem_kind():
    syms = _symbols()
    di = next((s for s in syms if s.name == "MyTable" and "dataitem(" in s.signature), None)
    assert di is not None
    assert di.kind == "type"


# --- XMLport Elements ---

def test_al_xmlport_textelement_extracted():
    syms = _symbols()
    elems = [s for s in syms if s.name == "Root" and "textelement(" in s.signature]
    assert len(elems) == 1


def test_al_xmlport_tableelement_extracted():
    syms = _symbols()
    elems = [s for s in syms if s.name == "Item" and "tableelement(" in s.signature]
    assert len(elems) == 1


def test_al_xmlport_fieldelement_extracted():
    syms = _symbols()
    elems = [s for s in syms if s.name == "No" and "fieldelement(" in s.signature]
    assert len(elems) == 1


def test_al_xmlport_element_kind():
    syms = _symbols()
    elem = next((s for s in syms if s.name == "Root" and "textelement(" in s.signature), None)
    assert elem is not None
    assert elem.kind == "type"


# --- ControlAddIn Events ---

def test_al_controladdin_event_extracted():
    syms = _symbols()
    events = [s for s in syms if s.kind == "method" and "event " in s.signature]
    assert len(events) >= 2


def test_al_controladdin_event_with_params():
    syms = _symbols()
    ev = next((s for s in syms if s.name == "OnCallback" and "event " in s.signature), None)
    assert ev is not None
    assert "data: Text" in ev.signature


def test_al_controladdin_event_qualified_name():
    syms = _symbols()
    ev = next((s for s in syms if s.name == "OnReady" and "event " in s.signature), None)
    assert ev is not None
    assert ev.qualified_name == "MyAddIn.OnReady"


# --- Page Layout Fields ---

def test_al_page_layout_field_extracted():
    syms = _symbols()
    pfields = [s for s in syms if s.kind == "constant" and "field(" in s.signature
               and s.parent is not None and "My Page" in s.parent
               and "Rec." in s.signature]
    assert len(pfields) >= 1


# --- Variable Declarations ---

def test_al_variable_extracted():
    syms = _symbols()
    vars_ = [s for s in syms if s.name == "LocalVar" and s.kind == "constant"]
    assert len(vars_) >= 1
    assert "Record" in vars_[0].signature


def test_al_variable_in_proc():
    syms = _symbols()
    vars_ = [s for s in syms if s.name == "Total" and s.kind == "constant"]
    assert len(vars_) >= 1
    assert "Decimal" in vars_[0].signature


# --- Inline comment fallback ---

def test_al_inline_comment_docstring():
    syms = _symbols()
    proc = next((s for s in syms if s.name == "CalcTotal"), None)
    assert proc is not None
    assert "total amount" in proc.docstring.lower()


# --- Decorators ---

def test_al_event_subscriber_decorator():
    syms = _symbols()
    proc = next((s for s in syms if s.name == "OnBeforePost"), None)
    assert proc is not None
    assert any("EventSubscriber" in d for d in proc.decorators)


def test_al_business_event_decorator():
    syms = _symbols()
    proc = next((s for s in syms if s.name == "OnCustomEvent"), None)
    assert proc is not None
    assert any("BusinessEvent" in d for d in proc.decorators)


def test_al_integration_event_decorator():
    syms = _symbols()
    proc = next((s for s in syms if s.name == "OnIntegrationEvent"), None)
    assert proc is not None
    assert any("IntegrationEvent" in d for d in proc.decorators)


# --- Docstrings ---

def test_al_xml_doc_comment():
    syms = _symbols()
    proc = next((s for s in syms if s.name == "DocumentedProc"), None)
    assert proc is not None
    assert "Documented procedure" in proc.docstring


# --- Qualified names ---

def test_al_qualified_names():
    syms = _symbols()
    proc = next((s for s in syms if s.name == "MyMethod"), None)
    assert proc is not None
    assert proc.qualified_name == "My Codeunit.MyMethod"


def test_al_trigger_qualified_name():
    syms = _symbols()
    trigger = next((s for s in syms if s.name == "OnRun"), None)
    assert trigger is not None
    assert trigger.qualified_name == "My Codeunit.OnRun"


# --- General invariants ---

def test_al_symbols_have_line_numbers():
    syms = _symbols()
    for s in syms:
        assert s.line >= 1


def test_al_symbols_sorted_by_line():
    syms = _symbols()
    line_nums = [s.line for s in syms]
    assert line_nums == sorted(line_nums)


def test_al_symbol_ids_unique():
    syms = _symbols()
    ids = [s.id for s in syms]
    assert len(ids) == len(set(ids))


def test_al_language_field():
    syms = _symbols()
    for s in syms:
        assert s.language == "al"
