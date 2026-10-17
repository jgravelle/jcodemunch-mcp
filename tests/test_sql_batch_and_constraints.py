"""SQL batch separators, quoting modes, guarded DDL, and named constraints.

Every case here was measured against a real corpus before it was written, and
every one of them FAILS on the parser as it stood before this file existed.

The common thread is that the derekstride/tree-sitter-sql grammar does not
merely *skip* what it cannot parse -- it opens an ERROR node that swallows
every statement after it, so one unsupported construct costs the rest of the
file.  Two different answers were needed, and the order matters:

1. ``GO`` / ``/`` batches are parsed SEPARATELY, so an unparseable batch costs
   exactly one batch.  ⚠⚠ Rewriting ``GO`` to ``;`` was tried FIRST and is not
   enough -- an ERROR node spans a substituted terminator happily, and on
   AdventureWorks' instawdb.sql one SQLCMD ``$(DatabaseName)`` still cost all
   71 CREATE TABLEs below it.  That rewrite is gone; isolation replaced it.
2. Within a batch, ``ALTER TABLE`` and ``IF [NOT] EXISTS (...)`` are blanked in
   a LENGTH-PRESERVING copy -- the grammar has no rule for either, and the
   constraints inside them are recovered by source scan instead, so nothing is
   thrown away.

⚠ Length preservation is load-bearing for (2): every byte offset the grammar
reports is used to slice the ORIGINAL source for names, signatures and content
hashes.  ``test_neutralisers_preserve_length_and_lines`` is what keeps it that
way, because a "tidy-up" returning a shorter buffer would corrupt every symbol
in the file rather than fail loudly.  ⚠ And (1) needs its own guard, because
batches are parsed as SLICES: see
``test_symbol_coordinates_are_whole_file_not_batch_relative``.
"""

import pytest

from jcodemunch_mcp.parser.extractor import (
    _mask_sql_noise,
    _neutralize_sql_alter_statements,
    _neutralize_sql_conditional_guards,
    _parse_sql_symbols,
)
from jcodemunch_mcp.parser.symbols import VALID_KINDS


def _parse(sql: str):
    return _parse_sql_symbols(sql.encode("utf-8"), "t.sql")


def _named(sql: str, kind=None) -> set:
    return {s.name for s in _parse(sql) if kind is None or s.kind == kind}


# --------------------------------------------------------------------------
# GO: a client command, not SQL
# --------------------------------------------------------------------------

GO_ONLY = (
    "CREATE TABLE dbo.Invoice (InvoiceId int NOT NULL)\n"
    "GO\n"
    "CREATE NONCLUSTERED INDEX IX_Inv ON dbo.Invoice (CustId)\n"
    "GO\n"
    "CREATE VIEW dbo.V_Inv AS SELECT 1\n"
    "GO\n"
)


def test_go_separated_script_yields_every_object():
    """Measured pre-fix: 1 of 3.  Bare GO is how SSMS and Sybase both export."""
    assert _named(GO_ONLY) == {"dbo.Invoice", "IX_Inv", "dbo.V_Inv"}


def test_semicolons_still_work_with_and_without_go():
    with_go = GO_ONLY.replace(")\nGO", ");\nGO").replace("1\nGO", "1;\nGO")
    semis_only = "\n".join(
        line for line in with_go.splitlines() if line.strip().upper() != "GO"
    )
    expected = {"dbo.Invoice", "IX_Inv", "dbo.V_Inv"}
    assert _named(with_go) == expected
    assert _named(semis_only) == expected


@pytest.mark.parametrize("sep", ["GO", "go", "  GO", "GO 5", "GO;", "GO -- next"])
def test_batch_separator_bounds_the_routine_it_follows(sep):
    """GO N and GO; are separators too.  Matching only bare GO left the
    previous routine's byte range running on into the separator line."""
    sql = (
        "CREATE PROCEDURE dbo.P AS SELECT 1\n"
        + sep
        + "\nCREATE VIEW dbo.V AS SELECT 2\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert set(syms) == {"dbo.P", "dbo.V"}
    assert syms["dbo.P"].end_line == 1, "separator did not bound the routine"


def test_go_inside_a_comment_or_string_is_not_a_separator():
    sql = (
        "CREATE PROCEDURE dbo.P AS\n"
        "BEGIN\n"
        "-- GO\n"
        "    PRINT 'GO'\n"
        "    SELECT 1\n"
        "END\n"
        "GO\n"
        "CREATE VIEW dbo.V AS SELECT 2\n"
        "GO\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert set(syms) == {"dbo.P", "dbo.V"}
    assert syms["dbo.P"].end_line >= 6, "a masked GO ended the routine early"


# --------------------------------------------------------------------------
# SET QUOTED_IDENTIFIER: what a double quote MEANS is a runtime setting
# --------------------------------------------------------------------------

QUOTED_OFF = (
    "SET QUOTED_IDENTIFIER OFF\n"
    "GO\n"
    "CREATE PROCEDURE dbo.P_Real AS\n"
    "BEGIN\n"
    '    PRINT "CREATE PROCEDURE dbo.P_Phantom AS SELECT 1"\n'
    "    PRINT 'CREATE PROCEDURE dbo.P_SingleQuoted AS SELECT 1'\n"
    "END\n"
    "GO\n"
    "SET QUOTED_IDENTIFIER ON\n"
    "GO\n"
    'CREATE TABLE "dbo"."QuotedTable" (id int)\n'
    "GO\n"
)


def test_quoted_identifier_off_makes_double_quotes_a_string():
    """Sybase ASE's default.  Pre-fix this emitted dbo.P_Phantom as a real
    procedure, because only single quotes were ever masked."""
    names = _named(QUOTED_OFF)
    assert "dbo.P_Real" in names
    assert "dbo.P_Phantom" not in names
    assert "dbo.P_SingleQuoted" not in names


def test_quoted_identifier_on_keeps_double_quotes_as_a_name():
    """The other half, and the reason the scan starts in the ON state: masking
    unconditionally would delete a real object instead of a phantom one."""
    assert "dbo.QuotedTable" in _named(QUOTED_OFF, "table")


def test_masking_defaults_to_identifier_semantics_without_a_set():
    src = b'CREATE TABLE "dbo"."T" (id int)'
    assert _mask_sql_noise(src) == src


# --------------------------------------------------------------------------
# Named constraints: PK / FK / UNIQUE / CHECK / DEFAULT
# --------------------------------------------------------------------------

SSMS_TABLE_SCRIPT = (
    "CREATE TABLE [dbo].[Invoice](\n"
    "\t[InvoiceId] [int] IDENTITY(1,1) NOT NULL,\n"
    "\t[CustId] [int] NOT NULL,\n"
    " CONSTRAINT [PK_Invoice] PRIMARY KEY CLUSTERED ([InvoiceId] ASC)\n"
    ") ON [PRIMARY]\n"
    "GO\n"
    "ALTER TABLE [dbo].[Invoice] ADD  CONSTRAINT [DF_Invoice_Created]"
    "  DEFAULT (getdate()) FOR [Created]\n"
    "GO\n"
    "ALTER TABLE [dbo].[Invoice]  WITH CHECK ADD  CONSTRAINT [FK_Invoice_Cust]"
    " FOREIGN KEY([CustId])\n"
    "REFERENCES [dbo].[Cust] ([CustId])\n"
    "GO\n"
    "CREATE NONCLUSTERED INDEX [IX_Invoice_Cust] ON [dbo].[Invoice] ([CustId] ASC)\n"
    "GO\n"
)


def test_ssms_script_table_as_create_layout_is_fully_recovered():
    """The most common file-per-table layout there is.  Measured pre-fix: 1 of
    5 -- the table only, with every constraint AND the index lost."""
    got = {(s.kind, s.name) for s in _parse(SSMS_TABLE_SCRIPT)}
    assert got == {
        ("table", "dbo.Invoice"),
        ("constraint", "PK_Invoice"),
        ("constraint", "DF_Invoice_Created"),
        ("constraint", "FK_Invoice_Cust"),
        ("index", "IX_Invoice_Cust"),
    }


def test_constraints_name_the_table_they_constrain():
    """``parent`` is the table's symbol id, the rule a trigger follows; the
    table is also named in the signature."""
    syms = _parse(SSMS_TABLE_SCRIPT)
    table_id = next(s.id for s in syms if s.name == "dbo.Invoice")
    constraints = [s for s in syms if s.kind == "constraint"]
    assert {s.name: s.parent for s in constraints} == {
        # inline: parent comes from the table's byte range
        "PK_Invoice": table_id,
        # ALTER: parent comes from the ALTER header
        "DF_Invoice_Created": table_id,
        "FK_Invoice_Cust": table_id,
    }
    assert all(s.signature.endswith(" ON dbo.Invoice") for s in constraints)


@pytest.mark.parametrize(
    "ddl,name",
    [
        ("ALTER TABLE dbo.T ADD CONSTRAINT PK_T PRIMARY KEY (id)", "PK_T"),
        (
            "ALTER TABLE dbo.T ADD CONSTRAINT FK_T FOREIGN KEY (a)"
            " REFERENCES dbo.U(a)",
            "FK_T",
        ),
        ("ALTER TABLE dbo.T ADD CONSTRAINT DF_T DEFAULT (0) FOR a", "DF_T"),
        ("ALTER TABLE dbo.T ADD CONSTRAINT UQ_T UNIQUE (a)", "UQ_T"),
        ("ALTER TABLE dbo.T ADD CONSTRAINT CK_T CHECK (a > 0)", "CK_T"),
    ],
)
def test_every_alter_table_constraint_spelling_is_found(ddl, name):
    """All five returned an EMPTY symbol list pre-fix, even alone in a file.

    The table is not defined in this file, so there is no id to parent to; it
    is still named in the signature."""
    syms = _parse(ddl)
    assert [(s.kind, s.name, s.parent) for s in syms] == [
        ("constraint", name, None)
    ]
    assert syms[0].signature.endswith(" ON dbo.T")


def test_unnamed_foreign_key_is_recovered_under_a_derived_name():
    """A nameless edge is not a missing edge -- Chinook's SQLite DDL and most
    ORM-generated schemas emit FKs with no CONSTRAINT clause at all."""
    anon = (
        "CREATE TABLE [A] ([id] INTEGER NOT NULL, [b] INTEGER,\n"
        "  CONSTRAINT [PK_A] PRIMARY KEY ([id]),\n"
        "  FOREIGN KEY ([b]) REFERENCES [B] ([id]) ON DELETE NO ACTION\n"
        ");\n"
    )
    named = anon.replace(
        "  FOREIGN KEY ([b])", "  CONSTRAINT [FK_A_B] FOREIGN KEY ([b])"
    )
    assert _named(anon, "constraint") == {"PK_A", "FK_A_B"}
    # The derived name matches what that FK is called when it IS named.
    assert _named(anon, "constraint") == _named(named, "constraint")


def test_constraint_is_a_valid_kind():
    assert "constraint" in VALID_KINDS


# --------------------------------------------------------------------------
# Guarded DDL
# --------------------------------------------------------------------------

def test_conditional_guard_does_not_hide_the_ddl_it_guards():
    """Migration house style.  Found twice independently: an indented
    CREATE UNIQUE INDEX in a real migration, and 10 of 10 indexes in the
    upstream tsql dialect fixture add_index.sql."""
    sql = (
        "IF NOT EXISTS(SELECT * FROM sys.indexes WHERE NAME='IX_A')\n"
        "    CREATE NONCLUSTERED INDEX IX_A ON dbo.T (c);\n"
        "GO\n"
        "IF EXISTS(SELECT * FROM sys.objects WHERE name = 'V_A')\n"
        "    CREATE VIEW dbo.V_A AS SELECT 1;\n"
        "GO\n"
    )
    assert _named(sql) == {"IX_A", "dbo.V_A"}


# --------------------------------------------------------------------------
# Routine ranges
# --------------------------------------------------------------------------

def test_routine_range_stops_at_a_statement_that_cannot_be_nested():
    """Without this the last routine in a GO-less file ran to EOF and adopted
    everything after it as a child."""
    sql = (
        "CREATE PROCEDURE dbo.P AS\n"
        "BEGIN\n"
        "    SELECT 1;\n"
        "END;\n"
        "CREATE VIEW dbo.V AS SELECT 2;\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert set(syms) == {"dbo.P", "dbo.V"}
    assert syms["dbo.V"].parent is None


def test_temp_tables_inside_a_routine_are_still_dropped():
    """The boundary deliberately excludes CREATE TABLE/INDEX: both appear at
    column 0 inside real procedure bodies, so treating either as a top-level
    boundary would truncate the routine instead of ending it."""
    sql = (
        "CREATE PROCEDURE dbo.P AS\n"
        "BEGIN\n"
        "CREATE TABLE #Temp (amount int)\n"
        "SELECT 1\n"
        "END\n"
        "GO\n"
    )
    assert [s.name for s in _parse(sql)] == ["dbo.P"]


# --------------------------------------------------------------------------
# The invariant all three rewrites depend on
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fn",
    [
            _neutralize_sql_alter_statements,
        _neutralize_sql_conditional_guards,
    ],
)
def test_neutralisers_preserve_length_and_lines(fn):
    """Every byte offset the grammar reports indexes the ORIGINAL source.  A
    rewrite that changed length would silently corrupt every name, signature
    and content hash in the file instead of failing."""
    src = (
        SSMS_TABLE_SCRIPT.encode("utf-8")
        + GO_ONLY.encode("utf-8")
        + b"IF NOT EXISTS(SELECT 1) CREATE INDEX IX ON dbo.T (c);\n"
    )
    out = fn(src, _mask_sql_noise(src))
    assert len(out) == len(src)
    assert out.count(b"\n") == src.count(b"\n")


# --------------------------------------------------------------------------
# Batch isolation
# --------------------------------------------------------------------------

def test_an_unparseable_batch_costs_only_itself():
    """⚠⚠ The load-bearing property, and the reason batches are parsed
    SEPARATELY rather than joined by a substituted terminator.

    Rewriting `GO` to `;` was tried first and is NOT enough: an ERROR node
    spans a substituted terminator happily, so on AdventureWorks' own
    instawdb.sql one SQLCMD `$(DatabaseName)` still cost all 71 CREATE TABLEs
    below it. Per-batch parsing bounds the damage at one batch, which is the
    only bound that holds against constructs nobody has thought of yet.
    """
    poison = "DROP DATABASE $(DatabaseName);"   # SQLCMD, not SQL
    sql = (
        "CREATE TABLE dbo.Before (a int)\n"
        "GO\n"
        f"{poison}\n"
        "GO\n"
        "CREATE TABLE dbo.After (b int)\n"
        "GO\n"
        "CREATE VIEW dbo.V AS SELECT 1\n"
        "GO\n"
    )
    names = _named(sql)
    assert "dbo.Before" in names, "the batch before the bad one was lost"
    assert "dbo.After" in names, "an unparseable batch swallowed the ones after it"
    assert "dbo.V" in names


def test_batch_spans_ignore_a_separator_inside_a_string_or_comment():
    from jcodemunch_mcp.parser.extractor import _sql_batch_spans

    src = (
        "CREATE PROCEDURE dbo.P AS\n"
        "BEGIN\n"
        "-- GO\n"
        "    PRINT 'GO'\n"
        "END\n"
        "GO\n"
    ).encode("utf-8")
    spans = _sql_batch_spans(_mask_sql_noise(src), len(src))
    assert len(spans) == 2, spans  # the body, then the empty tail after GO


def test_symbol_coordinates_are_whole_file_not_batch_relative():
    """Batches are parsed as slices, so every line and byte offset has to be
    stamped back into whole-file space.  An off-by-batch here would corrupt
    every symbol past the first separator rather than fail loudly."""
    sql = (
        "CREATE TABLE dbo.First (a int)\n"
        "GO\n"
        "\n\n\n"
        "CREATE TABLE dbo.Second (b int)\n"
        "GO\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert syms["dbo.First"].line == 1
    assert syms["dbo.Second"].line == 6, syms["dbo.Second"].line
    raw = sql.encode("utf-8")
    second = syms["dbo.Second"]
    sliced = raw[second.byte_offset:second.byte_offset + second.byte_length]
    assert sliced.startswith(b"CREATE TABLE dbo.Second"), sliced[:40]


def test_a_routine_body_does_not_swallow_the_objects_after_it():
    """⚠⚠ The case batch isolation CANNOT reach: a dialect with no batch
    separator at all.

    DB2's sakila schema is one long `;`-terminated batch alternating triggers
    and tables, and a compound `BEGIN ... END` body is exactly what the grammar
    turns into an ERROR node that swallows everything after it.  Measured: 1
    table of 16 before, 16 after.

    The fix is a SECOND pass over the batch with routine bodies blanked, and it
    is a UNION with the first, never a replacement -- blanking alone was
    measured and trades losses (DB2 gains 15 tables, PostgreSQL loses one).
    Blanking throws nothing away because the routine source scan already owns
    those bodies.
    """
    sql = (
        "CREATE TABLE first_t (a int);\n"
        "CREATE OR REPLACE TRIGGER t_before_update\n"
        "BEFORE UPDATE ON first_t\n"
        "REFERENCING NEW AS new\n"
        "FOR EACH ROW\n"
        "BEGIN\n"
        "  SET new.a = 1;\n"
        "END;\n"
        "CREATE TABLE second_t (b int);\n"
        "CREATE TABLE third_t (c int);\n"
    )
    found = {(s.kind, s.name) for s in _parse(sql)}
    assert ("table", "second_t") in found, "a routine body swallowed what followed"
    assert ("table", "third_t") in found
    assert ("trigger", "t_before_update") in found
    assert ("table", "first_t") in found


def test_the_two_passes_do_not_double_count():
    """Both passes see everything outside a routine, so the merge is on
    COORDINATES.  Deduping on the symbol id instead would hit the collision
    rule and turn the second sighting of one table into `T@L1`."""
    sql = (
        "CREATE TABLE only_once (a int);\n"
        "CREATE TRIGGER trg ON only_once FOR INSERT AS SELECT 1\n"
        "GO\n"
    )
    tables = [s for s in _parse(sql) if s.kind == "table"]
    assert len(tables) == 1, [(t.name, t.qualified_name, t.line) for t in tables]
    assert tables[0].qualified_name == "only_once"


def test_a_temp_table_does_not_end_the_routine_that_declares_it():
    """The routine boundary stops at a PERMANENT CREATE TABLE, because a
    dialect with no batch separator needs some boundary.  It must not stop at a
    temp table: real procedure bodies open `CREATE TABLE #Temp` at column 0."""
    sql = (
        "CREATE PROCEDURE dbo.P AS\n"
        "BEGIN\n"
        "CREATE TABLE #Temp (a int)\n"
        "SELECT 1\n"
        "END\n"
        "GO\n"
        "CREATE TABLE dbo.Real (b int)\n"
        "GO\n"
    )
    found = {(s.kind, s.name) for s in _parse(sql)}
    assert found == {("procedure", "dbo.P"), ("table", "dbo.Real")}


def test_blanking_range_and_routine_extent_are_not_the_same_range():
    """⚠⚠ Conflating them broke a standing contract, caught by the suite and
    not by review.

    A permanent table declared inside a deploy procedure is REAL and is
    re-parented onto that procedure rather than dropped, which needs the
    routine's FULL extent. Blanking wants the opposite bound -- it must stop
    before any permanent table, or a dialect with no batch separator blanks its
    own tables away. Both behaviours are asserted here together, because it is
    the combination that a future simplification would break.
    """
    sql = (
        "CREATE PROCEDURE dbo.Deploy\n"
        "AS\n"
        "CREATE TABLE dbo.NewThing (id INT)\n"
        "GO\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert syms["dbo.NewThing"].kind == "table"
    # full extent: the table is INSIDE the procedure, so it is scoped to it
    assert syms["dbo.NewThing"].parent == syms["dbo.Deploy"].id
    # tight blanking bound: the table survived the routine-blanked second pass
    assert "dbo.Deploy" in syms


# --------------------------------------------------------------------------
# Bracket-quoted identifiers
# --------------------------------------------------------------------------

def test_a_bracket_quoted_keyword_column_does_not_cost_its_table():
    """⚠⚠ One column named `[Name]` cost the ENTIRE `CREATE TABLE`.

    Measured on Chinook's SQLite schema: 4 of 11 tables lost to exactly this.
    The grammar mishandles a bracket-quoted identifier that collides with one
    of its keywords; the same table parses when the brackets are dropped, which
    is what the rewrite does in the grammar's copy only.
    """
    sql = (
        "CREATE TABLE [Playlist]\n"
        "(\n"
        "    [PlaylistId] INTEGER  NOT NULL,\n"
        "    [Name] NVARCHAR(120),\n"
        "    CONSTRAINT [PK_Playlist] PRIMARY KEY  ([PlaylistId])\n"
        ");\n"
    )
    found = {(s.kind, s.name) for s in _parse(sql)}
    assert ("table", "Playlist") in found
    assert ("constraint", "PK_Playlist") in found


def test_bracket_rewrite_keeps_schema_qualified_names_intact():
    """The rewrite turns `[dbo].[X]` into `dbo  .  X`, and names are read back
    out of THAT buffer -- so `_clean_sql_name` collapsing the spaces is what
    keeps the name right.  A regression here would rename every object in every
    bracket-quoted script."""
    sql = "CREATE VIEW [dbo].[AllWidgets] AS SELECT 1;\n"
    syms = _parse(sql)
    assert [(s.kind, s.name) for s in syms] == [("view", "dbo.AllWidgets")]


def test_names_that_are_not_plain_words_keep_their_brackets():
    r"""⚠ Only `[\w+]` is rewritten.  A bracketed name containing a space means
    something the unbracketed form does not, so unbracketing it would change
    the text rather than just its spelling."""
    from jcodemunch_mcp.parser.extractor import (
        _neutralize_sql_bracket_identifiers,
    )

    src = b"CREATE TABLE [T] ([Column 0] INT, [Name] INT)"
    out = _neutralize_sql_bracket_identifiers(src, _mask_sql_noise(src))
    assert len(out) == len(src)
    assert b"[Column 0]" in out, "a name with a space was unbracketed"
    assert b"[Name]" not in out, "a plain word kept its brackets"


def test_a_table_level_check_constraint_does_not_cost_its_table():
    """⚠ Narrow trigger, wide cost: `CHECK (a BETWEEN 1 AND 5)` loses the whole
    CREATE TABLE, while `CHECK (a IN (1,2))` and `CHECK (a > 0)` do not.

    That was AdventureWorks' last three tables. Blanking the CHECK for the
    grammar loses nothing, because `_scan_sql_constraints` reads it from the
    original bytes -- the same argument as ALTER TABLE.
    """
    sql = (
        "CREATE TABLE [S].[T](\n"
        "    [a] [int] NOT NULL,\n"
        "    CONSTRAINT [CK_T_a] CHECK ([a] BETWEEN 1 AND 5)\n"
        ") ON [PRIMARY];\n"
        "GO\n"
    )
    found = {(s.kind, s.name) for s in _parse(sql)}
    assert ("table", "S.T") in found
    assert ("constraint", "CK_T_a") in found, "the constraint itself was lost"


def test_blanking_a_check_takes_its_separating_comma_with_it():
    """Leaving `[a] int NOT NULL,` then blanks then `)` is a trailing comma --
    its own parse error.  The fix would otherwise trade one failure for
    another, which is the mistake this whole area keeps inviting."""
    from jcodemunch_mcp.parser.extractor import (
        _neutralize_sql_check_constraints,
    )

    src = (
        b"CREATE TABLE T (\n"
        b"    a int NOT NULL,\n"
        b"    CONSTRAINT CK_a CHECK (a BETWEEN 1 AND 5)\n"
        b")"
    )
    out = _neutralize_sql_check_constraints(src, _mask_sql_noise(src))
    assert len(out) == len(src)
    assert out.count(b"\n") == src.count(b"\n")
    assert b"CHECK" not in out
    assert b"NOT NULL," not in out, "the separating comma was left dangling"


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE #Temp (a int)",            # T-SQL / Sybase marker
        "CREATE TABLE @Var (a int)",             # table variable
        "CREATE TEMPORARY TABLE tmpCustomer (a int)",   # PostgreSQL keyword
        "CREATE TEMP TABLE tmpCustomer (a int)",        # short spelling
    ],
)
def test_temp_tables_are_recognised_by_marker_or_by_keyword(ddl):
    """⚠ TWO spellings of "not a schema object": T-SQL marks the NAME (`#`),
    PostgreSQL/MySQL mark the STATEMENT (`TEMPORARY`).  Knowing only the marker
    leaked a temp table declared in a PostgreSQL function body out as a
    top-level table -- measured on sakila, where it inflated the count by one
    in two different dialects."""
    sql = (
        "CREATE PROCEDURE dbo.P AS\nBEGIN\n"
        f"{ddl}\nSELECT 1\nEND\nGO\n"
    )
    assert [s.kind for s in _parse(sql)] == ["procedure"]


# --------------------------------------------------------------------------
# A routine ends at its own terminator (#1080) -- and the batch passes
# --------------------------------------------------------------------------

def test_a_terminated_routine_leaves_the_objects_after_it_found_once_and_top_level():
    """The second pass blanks the routine's extent. With a PostgreSQL body that
    ends at `$$;` the extent is one statement, so the table, view and index
    after it come out of the first pass AND the second and are merged on
    coordinates: once each, with no parent."""
    sql = (
        "CREATE OR REPLACE PROCEDURE p_one() LANGUAGE plpgsql AS $$ BEGIN NULL; END; $$;\n"
        "CREATE TABLE orders (id int primary key);\n"
        "CREATE VIEW v_orders AS SELECT id FROM orders;\n"
        "CREATE INDEX ix_orders ON orders (id);\n"
    )
    syms = _parse(sql)
    by_kind = {}
    for s in syms:
        by_kind.setdefault(s.kind, []).append(s)
    assert [s.name for s in by_kind["table"]] == ["orders"]
    assert [s.name for s in by_kind["view"]] == ["v_orders"]
    assert [s.name for s in by_kind["index"]] == ["ix_orders"]
    assert [(s.name, s.line, s.end_line) for s in by_kind["procedure"]] == [("p_one", 1, 1)]
    assert all(s.parent is None for k in ("table", "view", "index") for s in by_kind[k])


def test_a_batch_holding_a_mysql_delimiter_routine_keeps_the_next_batch_top_level():
    sql = (
        "DELIMITER ;;\n"
        "CREATE PROCEDURE p_one()\n"
        "BEGIN\n"
        "  SELECT 1;\n"
        "END ;;\n"
        "DELIMITER ;\n"
        "CREATE TABLE orders (id INT);\n"
        "GO\n"
        "CREATE TABLE items (id INT);\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert (syms["p_one"].line, syms["p_one"].end_line) == (2, 5)
    assert syms["orders"].parent is None
    assert syms["items"].parent is None


def test_a_bracketed_constraint_name_is_read_from_the_source_not_the_mask():
    """The mask fills a bracketed name with underscores so an apostrophe or a
    keyword inside it opens and closes nothing; the scans that read names must
    take them from the original bytes."""
    sql = (
        "CREATE TABLE [dbo].[Orders] ([Id] int NOT NULL)\n"
        "GO\n"
        "ALTER TABLE [dbo].[Orders] ADD CONSTRAINT [PK_it's] PRIMARY KEY ([Id])\n"
        "GO\n"
        "ALTER TABLE [dbo].[Orders] ADD CONSTRAINT [FK_Orders_Cust] FOREIGN KEY ([Id])"
        " REFERENCES [dbo].[Customers] ([Id])\n"
        "GO\n"
        "CREATE VIEW [dbo].[V_After] AS SELECT 1\n"
    )
    syms = _parse(sql)
    assert {s.name for s in syms if s.kind == "constraint"} == {"PK_it's", "FK_Orders_Cust"}
    assert "dbo.V_After" in {s.name for s in syms}


def test_a_bracketed_anonymous_foreign_key_target_keeps_its_name():
    sql = (
        "CREATE TABLE a (id int, c_id int, CONSTRAINT [PK_a] PRIMARY KEY (id),"
        " FOREIGN KEY (c_id) REFERENCES [Customers] (id))\n"
    )
    names = {s.name for s in _parse(sql) if s.kind == "constraint"}
    assert names == {"PK_a", "FK_a_Customers"}


def test_only_a_plain_word_is_unbracketed_for_the_grammar():
    from jcodemunch_mcp.parser.extractor import _neutralize_sql_bracket_identifiers

    buf = b"SELECT [Name], [Column 0], [it's] FROM [T]"
    out = _neutralize_sql_bracket_identifiers(buf, _mask_sql_noise(buf))
    assert out == b"SELECT Name  , [Column 0], [it's] FROM T  "


def test_parser_generation_advanced_for_the_batch_parse():
    """The rows change on unchanged content (objects that were lost appear),
    so an index stamped with the previous generation must be re-parsed."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 23


def test_an_oracle_declaration_section_routine_meets_the_two_passes():
    """`IS v NUMBER; BEGIN ... END p;` ends at its own END, so the table and
    index after it come out of both passes once each and with no parent."""
    sql = (
        "CREATE OR REPLACE PROCEDURE p_o IS\n"
        "  v NUMBER;\n"
        "BEGIN\n"
        "  v := 1;\n"
        "END p_o;\n"
        "CREATE TABLE t8 (id int);\n"
        "CREATE INDEX ix8 ON t8 (id);\n"
    )
    syms = _parse(sql)
    proc = next(s for s in syms if s.name == "p_o")
    assert (proc.line, proc.end_line) == (1, 5)
    assert [s.name for s in syms if s.kind == "table"] == ["t8"]
    assert [s.name for s in syms if s.kind == "index"] == ["ix8"]
    assert all(s.parent is None for s in syms if s.kind in ("table", "index"))


def test_an_oracle_trigger_with_a_declare_section_leaves_the_table_after_it_found():
    sql = (
        "CREATE OR REPLACE TRIGGER trg_o BEFORE INSERT ON t8 FOR EACH ROW\n"
        "DECLARE\n"
        "  v NUMBER;\n"
        "BEGIN\n"
        "  v := 1;\n"
        "END;\n"
        "CREATE TABLE t8 (id int);\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert syms["trg_o"].end_line == 6
    assert syms["t8"].parent is None


def test_an_oracle_function_with_a_declaration_section_ends_at_its_own_end():
    sql = (
        "CREATE OR REPLACE FUNCTION f_o(a NUMBER) RETURN NUMBER IS\n"
        "  CURSOR c IS SELECT 1 FROM dual;\n"
        "  PROCEDURE local_p IS BEGIN NULL; END local_p;\n"
        "BEGIN\n"
        "  RETURN a;\n"
        "END f_o;\n"
        "CREATE TABLE t8 (id int);\n"
    )
    syms = {s.name: s for s in _parse(sql)}
    assert (syms["f_o"].kind, syms["f_o"].line, syms["f_o"].end_line) == ("function", 1, 6)
    assert syms["t8"].parent is None
