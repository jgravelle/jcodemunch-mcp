/**
 * MUNCH decoder — TypeScript reference implementation.
 *
 * Companion to the canonical Python decoder at
 * `jcodemunch_mcp.encoding.decoder.decode`. Decodes the on-wire format
 * described in SPEC_MUNCH.md (version 1) back to a plain JS object.
 *
 * Usage:
 *     import { decode } from "./decoder";
 *     const obj = decode(payload);
 *
 * Falls through to `JSON.parse` for payloads that don't start with the
 * MUNCH header, so callers can hand this any tool-response string without
 * branching first.
 *
 * The decoder is intentionally schema-free: it reads the embedded
 * `__tables` / `__stypes` hints that every MUNCH payload carries and
 * reconstructs the original response shape (keys, column order, types).
 * Tier-1 encoder payloads (find_references, dependency_graph, ...) and
 * generic-fallback payloads (gen1) both decode through this single path.
 */

const HEADER_PREFIX = "#MUNCH/";

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export function decode(payload: string): Json {
    if (!payload.startsWith(HEADER_PREFIX)) {
        return JSON.parse(payload);
    }
    const lines = payload.split("\n");
    const header = lines.shift() ?? "";
    parseHeader(header);

    // Re-split into blocks separated by blank lines.
    const blocks: string[] = [];
    let buf: string[] = [];
    for (const ln of lines) {
        if (ln === "") {
            if (buf.length) {
                blocks.push(buf.join("\n"));
                buf = [];
            }
        } else {
            buf.push(ln);
        }
    }
    if (buf.length) blocks.push(buf.join("\n"));

    // Classify blocks: legend (@N=...), scalar (key=...), or table (<c>,...).
    const tableTags = "abcdefghijklmnopqrstuvwxyz";
    const legend = new Map<number, string>();
    let scalarBlock: string | null = null;
    const tableBlocks: string[] = [];

    for (const b of blocks) {
        const first = b.split("\n", 1)[0] ?? "";
        const isTableRow =
            first.length >= 2 && first[1] === "," && tableTags.includes(first[0]);
        if (first.startsWith("@") && first.includes("=") && !isTableRow) {
            for (const ln of b.split("\n")) {
                if (!ln.startsWith("@")) continue;
                const eq = ln.indexOf("=");
                if (eq < 0) continue;
                const n = parseInt(ln.slice(1, eq), 10);
                if (Number.isFinite(n)) legend.set(n, ln.slice(eq + 1));
            }
        } else if (isTableRow) {
            tableBlocks.push(b);
        } else if (scalarBlock === null && first.includes("=")) {
            scalarBlock = b;
        } else {
            tableBlocks.push(b);
        }
    }

    const rawScalars = scalarBlock ? parseScalars(scalarBlock) : new Map<string, string>();
    const tablesSpec = parseTableSchemas(rawScalars.get("__tables") ?? "");
    rawScalars.delete("__tables");
    const scalarTypes = parseScalarTypes(rawScalars.get("__stypes") ?? "");
    rawScalars.delete("__stypes");

    const result: Record<string, Json> = {};
    const meta: Record<string, Json> = {};

    for (const [k, v] of rawScalars) {
        if (k.startsWith("__json.")) {
            const real = k.slice("__json.".length);
            try {
                result[real] = JSON.parse(v);
            } catch {
                result[real] = v;
            }
        } else if (k.startsWith("_meta.")) {
            meta[k.slice("_meta.".length)] = coerceGuess(v);
        } else if (k.includes(".")) {
            // Dotted nested-dict form: parent.child=value
            const [parent, child] = splitOnce(k, ".");
            const bucket = (result[parent] as Record<string, Json> | undefined) ?? {};
            bucket[child] = coerceGuess(v);
            result[parent] = bucket;
        } else if (scalarTypes.has(k)) {
            result[k] = coerce(v, scalarTypes.get(k)!);
        } else {
            result[k] = v;
        }
    }
    if (Object.keys(meta).length > 0) {
        result._meta = meta;
    }

    for (const spec of tablesSpec) {
        const rows: Record<string, Json>[] = [];
        for (const block of tableBlocks) {
            for (const r of readTable(block, spec.tag)) {
                const row: Record<string, Json> = {};
                for (let i = 0; i < spec.cols.length; i++) {
                    const col = spec.cols[i];
                    const type = spec.types[i] ?? "str";
                    let raw = r[i] ?? "";
                    if (type === "str") raw = decodeLegendPrefix(raw, legend);
                    row[col] = coerce(raw, type);
                }
                rows.push(row);
            }
        }
        if (spec.key.includes(".")) {
            const [parent, child] = splitOnce(spec.key, ".");
            const bucket = (result[parent] as Record<string, Json> | undefined) ?? {};
            bucket[child] = rows;
            result[parent] = bucket;
        } else {
            result[spec.key] = rows;
        }
    }

    return result;
}

// --- helpers ----------------------------------------------------------------

function parseHeader(line: string): void {
    if (!line.startsWith(HEADER_PREFIX)) {
        throw new Error(`not a MUNCH payload: ${JSON.stringify(line.slice(0, 40))}`);
    }
}

function splitOnce(s: string, sep: string): [string, string] {
    const i = s.indexOf(sep);
    return i < 0 ? [s, ""] : [s.slice(0, i), s.slice(i + sep.length)];
}

type TableSpec = { tag: string; key: string; cols: string[]; types: string[] };

function parseTableSchemas(text: string): TableSpec[] {
    const out: TableSpec[] = [];
    for (const part of text.split(",")) {
        if (!part) continue;
        const pieces = part.split(":");
        if (pieces.length < 3) continue;
        const [tag, key, colSpec] = pieces;
        const typeSpec = pieces[3] ?? "";
        const cols = colSpec ? colSpec.split("|") : [];
        const rawTypes = typeSpec ? typeSpec.split("|") : [];
        const types = cols.map((_, i) => rawTypes[i] ?? "str");
        out.push({ tag, key, cols, types });
    }
    return out;
}

function parseScalarTypes(text: string): Map<string, string> {
    const map = new Map<string, string>();
    for (const part of text.split("|")) {
        if (!part) continue;
        const [name, type] = splitOnce(part, ":");
        if (name && type) map.set(name, type);
    }
    return map;
}

function parseScalars(line: string): Map<string, string> {
    // Single-line key=value pairs, whitespace separated. Values that contain
    // spaces, commas, equals, or quotes are RFC-4180 quoted with doubled-quote
    // escaping.
    const out = new Map<string, string>();
    const s = line.replace(/\n/g, " ");
    let i = 0;
    const n = s.length;
    while (i < n) {
        while (i < n && /\s/.test(s[i])) i++;
        if (i >= n) break;
        const eq = s.indexOf("=", i);
        if (eq < 0) break;
        const key = s.slice(i, eq);
        i = eq + 1;
        let value: string;
        if (i < n && s[i] === '"') {
            i++;
            const parts: string[] = [];
            while (i < n) {
                if (s[i] === '"') {
                    if (i + 1 < n && s[i + 1] === '"') {
                        parts.push('"');
                        i += 2;
                        continue;
                    }
                    i++;
                    break;
                }
                parts.push(s[i]);
                i++;
            }
            value = parts.join("");
        } else {
            const start = i;
            while (i < n && !/\s/.test(s[i])) i++;
            value = s.slice(start, i);
        }
        out.set(key, value);
    }
    return out;
}

function readTable(text: string, tag: string): string[][] {
    // Minimal RFC-4180 reader scoped to our CSV dialect: comma separator,
    // doubled-quote escape, newline row terminator.
    const rows: string[][] = [];
    const n = text.length;
    let i = 0;
    while (i < n) {
        const row: string[] = [];
        while (true) {
            let field: string;
            if (i < n && text[i] === '"') {
                i++;
                const buf: string[] = [];
                while (i < n) {
                    if (text[i] === '"') {
                        if (i + 1 < n && text[i + 1] === '"') {
                            buf.push('"');
                            i += 2;
                            continue;
                        }
                        i++;
                        break;
                    }
                    buf.push(text[i]);
                    i++;
                }
                field = buf.join("");
            } else {
                const start = i;
                while (i < n && text[i] !== "," && text[i] !== "\n") i++;
                field = text.slice(start, i);
            }
            row.push(field);
            if (i < n && text[i] === ",") {
                i++;
                continue;
            }
            break;
        }
        if (i < n && text[i] === "\n") i++;
        if (row.length && row[0] === tag) rows.push(row.slice(1));
    }
    return rows;
}

function decodeLegendPrefix(value: string, legend: Map<number, string>): string {
    if (!value || !value.startsWith("@")) return value;
    let j = 1;
    while (j < value.length && value[j] >= "0" && value[j] <= "9") j++;
    if (j === 1) return value;
    const idx = parseInt(value.slice(1, j), 10);
    const literal = legend.get(idx);
    return literal === undefined ? value : literal + value.slice(j);
}

function coerce(raw: string, hint: string): Json {
    if (raw === "") return null;
    // A `json` column carries each cell as JSON text: a list or dict, or a
    // scalar beside one (gen1).
    if (hint === "json") return JSON.parse(raw) as Json;
    if (hint === "bool") return raw === "T";
    if (hint === "int") {
        const n = parseInt(raw, 10);
        return Number.isFinite(n) ? n : raw;
    }
    if (hint === "float") {
        const n = parseFloat(raw);
        return Number.isFinite(n) ? n : raw;
    }
    return raw;
}

function coerceGuess(raw: string): Json {
    // Used for _meta.X and dotted-nested scalars, which don't carry a type hint.
    if (raw === "") return null;
    if (raw === "T") return true;
    if (raw === "F") return false;
    if (/^-?\d+$/.test(raw)) return parseInt(raw, 10);
    if (/^-?\d+\.\d+([eE][-+]?\d+)?$/.test(raw)) return parseFloat(raw);
    return raw;
}
