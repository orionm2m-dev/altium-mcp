"""Bounded DelphiScript preflight; not a compiler or native API certification."""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import re

CATALOG_PATH = Path(__file__).with_name("delphiscript_compatibility.json")
RESERVED = set("LogLines LogPath OutPath S1 S2 S3 I1 I2 I3 B1 Obj1 Obj2 Obj3 Obj4 Obj5 List1 IntMan DbDoc SandboxLog Run ResultText REPLACEALL".lower().split())
BUILTINS = set("SandboxLog StringReplace IntToStr FloatToStr FormatFloat FileExists SameText CoordToMMs MMsToCoord MilsToCoord CoordToMils Layer2String Int Floor Round Abs Sqrt Length Copy Pos Delete Insert Trim UpperCase LowerCase Assigned SetLength Include Exclude Ord Chr Inc Dec Exit RunProcess ResetParameters AddStringParameter AddIntegerParameter AddFloatParameter AddBooleanParameter GetWorkspace PCBServer SchServer Client".lower().split())

@dataclass
class Token:
    text: str
    line: int
    column: int
    kind: str = "code"
    @property
    def key(self):
        return self.text.lower()


def lex(source):
    """Keep locations; comments disappear and Pascal strings stay opaque."""
    out, issues = [], []
    i, line, col = 0, 1, 1
    while i < len(source):
        start, ln, cl = i, line, col
        kind = "code"
        if source[i].isspace() or source[i] == "\ufeff":
            i += 1
        elif source.startswith("//", i):
            i = source.find("\n", i)
            if i < 0: i = len(source)
        elif source[i] == "{" or source.startswith("(*", i):
            closing = "}" if source[i] == "{" else "*)"
            end = source.find(closing, i + (1 if closing == "}" else 2))
            if end < 0:
                issues.append((ln, cl, "Unterminated comment")); i = len(source)
            else: i = end + len(closing)
        elif source[i] == "'":
            i += 1; closed = False
            while i < len(source):
                if source[i] in "\r\n": break
                if source[i] == "'":
                    i += 1
                    if i < len(source) and source[i] == "'": i += 1; continue
                    closed = True; break
                i += 1
            if not closed: issues.append((ln, cl, "Unterminated string literal"))
            out.append(Token(source[start:i], ln, cl, "string"))
        else:
            match = re.match(r"[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|:=|<>|<=|>=|.", source[i:])
            i += len(match.group()); out.append(Token(match.group(), ln, cl, kind))
        piece = source[start:i]
        if "\n" in piece: line += piece.count("\n"); col = len(piece.rsplit("\n", 1)[1]) + 1
        else: col += len(piece)
    return out, issues

@dataclass
class Scope:
    start: int
    end: int
    parent: object = None
    symbols: dict = field(default_factory=dict)

class Analyzer:
    def __init__(self, tokens, source, findings, global_scope=None):
        self.t, self.source, self.findings = tokens, source, findings
        self.global_scope = global_scope or Scope(0, len(tokens))
        self.scopes = [self.global_scope]
        self.helpers = set()
        self.declaration_tokens = set()

    def diagnostic(self, token, code, message, severity="warning", **extra):
        self.findings.append(dict(severity=severity, code=code, source=self.source,
                                 line=token.line, column=token.column, message=message, **extra))

    def declare(self, scope, token, type_name, index):
        name = token.key
        if name in scope.symbols:
            self.diagnostic(token, "duplicate_declaration", "Duplicate declaration in the same scope: " + token.text, "error")
        if scope is self.global_scope and name in RESERVED:
            self.diagnostic(token, "sandbox_name_collision", "Declaration collides with sandbox name: " + token.text, "error")
        scope.symbols[name] = type_name.lower()
        self.declaration_tokens.add(index)

    def typed_group(self, i, scope, limit):
        """Only direct name[,name]: Type declarations; no inferred field types."""
        t = self.t; names = []
        while i < limit and re.fullmatch(r"[A-Za-z_]\w*", t[i].text):
            names.append(i); i += 1
            if i < limit and t[i].text == ",": i += 1; continue
            break
        if not names or i + 1 >= limit or t[i].text != ":": return None
        type_name = t[i+1].text
        for n in names: self.declare(scope, t[n], type_name, n)
        return i + 2

    def semicolon(self, i):
        depth = 0
        while i < len(self.t):
            k = self.t[i].text
            if k == "(": depth += 1
            elif k == ")": depth -= 1
            elif k == ";" and depth == 0: return i
            i += 1
        return i

    def block_end(self, i):
        depth = 0
        while i < len(self.t):
            k = self.t[i].key if self.t[i].kind == "code" else ""
            if k in {"begin", "case", "try", "record", "asm"}: depth += 1
            elif k == "end":
                depth -= 1
                if depth == 0: return i + 1
            i += 1
        return i

    def declarations(self, i, scope, stop_at_body=False):
        t = self.t
        while i < len(t):
            k = t[i].key
            if k in {"procedure", "function"} and i + 1 < len(t):
                name_index = i + 1; name = t[name_index].key
                self.helpers.add(name); self.declaration_tokens.add(name_index)
                if scope is self.global_scope and name in RESERVED:
                    self.diagnostic(t[name_index], "sandbox_name_collision", "Routine collides with sandbox name: " + name, "error")
                header_end = self.semicolon(i)
                child = Scope(i, len(t), scope); self.scopes.append(child)
                j = i + 2
                if j < header_end and t[j].text == "(":
                    j += 1
                    while j < header_end and t[j].text != ")":
                        if t[j].key in {"var", "const", "out"}: j += 1
                        after = self.typed_group(j, child, header_end)
                        j = after if after is not None else j + 1
                        while j < header_end and t[j].text not in {";", ")"}: j += 1
                        if j < header_end and t[j].text == ";": j += 1
                j = header_end + 1
                if j < len(t) and t[j].key in {"forward", "external"}:
                    i = self.semicolon(j) + 1; child.end = i; continue
                i = self.declarations(j, child, True); child.end = i
            elif k == "var":
                i += 1
                while i < len(t):
                    after = self.typed_group(i, scope, len(t))
                    if after is None: break
                    i = self.semicolon(after) + 1
            elif k == "const":
                i += 1
                while i + 1 < len(t) and t[i+1].text == "=":
                    self.declare(scope, t[i], "constant", i); i = self.semicolon(i) + 1
            elif k == "type":
                # Do not infer aliases/records or diagnose their fields as variables.
                i += 1
                while i + 1 < len(t) and t[i+1].text == "=":
                    self.diagnostic(t[i], "type_declaration_unresolved", "User types/aliases are not resolved by this preflight.")
                    i = self.semicolon(i) + 1
            elif k == "begin":
                i = self.block_end(i)
                if stop_at_body: return i + (i < len(t) and t[i].text == ";")
            else: i += 1
        return i

    def receiver_type(self, index, name):
        scope = min((s for s in reversed(self.scopes) if s.start <= index < s.end), key=lambda s:s.end-s.start, default=self.global_scope)
        while scope:
            if name in scope.symbols: return scope.symbols[name]
            scope = scope.parent
        return None

    def usages(self, catalog, extra_helpers=()):
        rules = {(r["receiver_type"].lower(),r["member"].lower()):r for r in catalog["incompatible_members"]}
        seen = set()
        has_with = any(t.kind == "code" and t.key == "with" for t in self.t)
        for i, token in enumerate(self.t):
            if token.kind != "code": continue
            if i + 2 < len(self.t) and self.t[i+1].text == "." and re.fullmatch(r"[A-Za-z_]\w*", self.t[i+2].text):
                member = self.t[i+2]; chained = i > 0 and self.t[i-1].text in {".", ")", "]"}
                receiver = None if chained or has_with else self.receiver_type(i, token.key)
                rule = rules.get((receiver, member.key))
                key = (receiver, token.key, member.key, bool(rule))
                if key in seen: continue
                seen.add(key)
                if rule:
                    self.diagnostic(member, rule["id"], rule["reason"], "error", receiver=token.text,
                                    receiver_type=receiver, member=member.text, evidence=rule["evidence"])
                else:
                    self.diagnostic(member, "unverified_member", "Member availability is not verified by the compatibility catalog.",
                                    receiver=token.text, receiver_type=receiver, member=member.text)
            if (i+1 < len(self.t) and self.t[i+1].text == "(" and re.fullmatch(r"[A-Za-z_]\w*", token.text)
                and i not in self.declaration_tokens and (i == 0 or self.t[i-1].text != ".")
                and token.key not in BUILTINS | self.helpers | set(extra_helpers) | {"if", "while", "case", "not", "and", "or"}):
                key = ("call",token.key)
                if key not in seen:
                    seen.add(key); self.diagnostic(token, "unresolved_standalone_call", "Standalone call is not declared here or in the small built-in-name list; verify its availability.")


def lint(script, declarations=""):
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    findings = []
    def scan(text, source):
        tokens, errors = lex(text)
        for ln, col, msg in errors:
            findings.append(dict(severity="error",code="lexical_error",source=source,line=ln,column=col,message=msg))
        return tokens
    decl = Analyzer(scan(declarations,"declarations"),"declarations",findings)
    decl.declarations(0,decl.global_scope)
    decl.usages(catalog)
    body_scope = Scope(0,0,decl.global_scope)
    body_tokens = scan(script,"script"); body_scope.end = len(body_tokens)
    body = Analyzer(body_tokens,"script",findings,body_scope)
    body.usages(catalog,decl.helpers)
    return dict(success=not any(f["severity"]=="error" for f in findings),
                status="rejected" if any(f["severity"]=="error" for f in findings) else "no_known_blockers_found",
                findings=findings, no_new_dispatch=True, native_compilation_verified=False,
                native_api_compatibility_verified=False, catalog_version=catalog["version"],
                inputs_sha256={"script":hashlib.sha256(script.encode()).hexdigest(),"declarations":hashlib.sha256(declarations.encode()).hexdigest()},
                limitations=["Not a DelphiScript compiler; syntax and runtime success are not certified.",
                             "Only listed typed receiver/member incompatibilities are blocking; all other member availability is unverified.",
                             "Direct var/parameter types and lexical routine scopes only; aliases, casts, dynamic dispatch and chained return types are unresolved. A source containing with disables typed-member rejection for that source.",
                             "Unknown standalone calls with parentheses are warnings; bare calls and external units are not resolved.",
                             "Duplicate checks cover simple variables/parameters/constants and sandbox collisions, not full Pascal declaration grammar."])


def prepare(script, declarations="", declarations_file=""):
    """Resolve the file exactly once so the checked bytes are dispatched."""
    if not declarations and declarations_file:
        try:
            declarations = Path(declarations_file).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            return "", dict(success=False,status="input_error",error=f"could not read declarations_file: {exc}",
                            no_new_dispatch=True,native_compilation_verified=False)
    return declarations, lint(script, declarations)
