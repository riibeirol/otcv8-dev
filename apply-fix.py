#!/usr/bin/env python3
"""Analisa log do MSBuild e aplica fixes conhecidos ao source.

Exit codes:
    0 — aplicou algum fix (source modificado)
    2 — erros que nao temos fix conhecido (stop)
    3 — mesmo padrao da ultima iteracao (loop detectado, stop)
"""
import os
import re
import sys
import json
from pathlib import Path

SRC = Path("/mnt/c/otcv8-dev/src")
STATE_FILE = Path("/tmp/auto-build/state.json")

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_fingerprint": None, "iters": 0}

def save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s))

def classify_errors(log: str):
    """Retorna set de tags representando classes de erros únicas no log."""
    tags = set()
    if "io_service.hpp" in log and "No such file" in log:
        tags.add("io_service_header")
    if "io_service" in log and "não é um membro de 'boost::asio'" in log:
        tags.add("io_service_typedef")
    if "'iterator'" in log and "basic_resolver" in log:
        tags.add("resolver_iterator")
    if "boost::process::child" in log or "'child'" in log and "boost::process" in log:
        tags.add("boost_process_child")
    if "startup_info" in log:
        tags.add("boost_process_startup_info")
    if "caractere desconhecido '0x1'" in log:
        tags.add("soh_bytes")
    if "cancel" in log and "função não recebe" in log and "argumentos" in log:
        tags.add("timer_cancel_args")
    if "ResolveHandler type requirements not met" in log or "basic_resolver_iterator" in log:
        tags.add("resolver_handler_signature")
    if "basic_resolver_iterator" in log and "private" in log and ("operator ->" in log or "operator *" in log):
        tags.add("resolver_results_deref")
    if "'string' in namespace 'std' does not name a type" in log or "'string' is not a member of 'std'" in log:
        tags.add("missing_include_string")
    if "'INT_MAX'" in log and ("não declarado" in log or "undeclared" in log or "identificador" in log):
        tags.add("missing_include_climits")
    if "'LLONG_MAX'" in log or "'LONG_MAX'" in log or "'INT_MIN'" in log:
        tags.add("missing_include_climits")
    if "MSB8020" in log and "v142" in log:
        tags.add("toolset_v142")
    if "vcvarsall.bat" in log and "Unable to find" in log:
        tags.add("vcpkg_vs_detect")
    if "Cannot open include file: 'boost/asio/io_service.hpp'" in log:
        tags.add("io_service_header")
    return tags

def apply_fix_io_service_header():
    """replace boost/asio/io_service.hpp por boost/asio/io_context.hpp"""
    changed = False
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "io_service.hpp" in c:
            c2 = c.replace("boost/asio/io_service.hpp", "boost/asio/io_context.hpp")
            if c2 != c:
                f.write_text(c2, encoding="utf-8")
                changed = True
    return changed

def apply_fix_io_service_typedef():
    """io_service -> io_context em todo source"""
    changed = False
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = c.replace("boost::asio::io_service", "boost::asio::io_context")
        new = new.replace("asio::io_service", "asio::io_context")
        if new != c:
            f.write_text(new, encoding="utf-8")
            changed = True
    return changed

def apply_fix_resolver_iterator():
    changed = False
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = c
        # tcp::resolver::iterator -> tcp::resolver::results_type::iterator
        new = re.sub(
            r"\b(asio::ip::tcp::resolver|boost::asio::ip::tcp::resolver|tcp::resolver)::iterator",
            r"\1::results_type::iterator", new,
        )
        new = re.sub(
            r"\bbasic_resolver<(asio::ip::tcp|boost::asio::ip::tcp|tcp)>::iterator",
            r"basic_resolver<\1>::results_type::iterator", new,
        )
        if new != c:
            f.write_text(new, encoding="utf-8")
            changed = True
    return changed

def apply_fix_soh_bytes():
    changed = False
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            raw = f.read_bytes()
        except Exception:
            continue
        if b"\x01" in raw:
            f.write_bytes(raw.replace(b"\x01", b""))
            changed = True
    return changed

def apply_fix_timer_cancel():
    changed = False
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # timer.cancel(anything) -> timer.cancel()
        new = re.sub(r"(m_timer|timer)\.cancel\([^)]+\)", r"\1.cancel()", c)
        if new != c:
            f.write_text(new, encoding="utf-8")
            changed = True
    return changed

def apply_fix_missing_include_string():
    """Procura headers que declaram std::string sem incluir <string>"""
    changed = False
    for f in SRC.rglob("*.h"):
        if not f.is_file():
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # se usa std::string mas não inclui <string> ainda
        if "std::string" in c and "<string>" not in c:
            # insere #include <string> logo apos o primeiro #include
            new = re.sub(r"(#include [<\"][^>\"]+[>\"])", r"\1\n#include <string>", c, count=1)
            if new != c:
                f.write_text(new, encoding="utf-8")
                changed = True
    return changed

def apply_fix_resolver_handler_signature():
    """on_resolve(ec, results_type::iterator) -> on_resolve(ec, results_type).
    Também ajusta uso interno: 'iterator->' -> 'iterator.begin()->', '*iterator' -> '*iterator.begin()'.
    Aplica só em session.cpp/websocket.cpp porque são os unicos usos documentados."""
    changed = False
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = c
        # troca parametro tipo: results_type::iterator -> results_type
        new = re.sub(
            r"(tcp::resolver::results_type|basic_resolver<[^>]+>::results_type)::iterator\s+(\w+)",
            r"\1 \2", new,
        )
        # Se o arquivo está em http/, ajusta usos do iterator (idempotente)
        if "http/" in str(f):
            # iterator->X -> iterator.begin()->X, só se ainda NÃO estiver na forma .begin()->
            new = re.sub(r"(?<!\.begin\(\))\biterator->", r"iterator.begin()->", new)
            # *iterator -> *iterator.begin(), só se ainda NÃO tiver .begin() após iterator
            new = re.sub(r"\*iterator\b(?!\.begin\(\))", r"*iterator.begin()", new)
            # dedup agressivo (caso runs antigas tenham empilhado)
            while "iterator.begin().begin()" in new:
                new = new.replace("iterator.begin().begin()", "iterator.begin()")
        if new != c:
            f.write_text(new, encoding="utf-8")
            changed = True
    return changed


def apply_fix_resolver_results_deref():
    """Dentro dos files http/, converte 'iterator->' em 'iterator.begin()->' e
    '*iterator' em '*iterator.begin()'. Usado quando on_resolve recebe
    results_type (novo boost) em vez do iterator."""
    changed = False
    for f in SRC.rglob("http/*"):
        if not f.is_file() or f.suffix.lower() not in {".cpp", ".h"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = c
        new = re.sub(r"(?<!\.begin\(\))\biterator->", r"iterator.begin()->", new)
        new = re.sub(r"\*iterator\b(?!\.begin\(\))", r"*iterator.begin()", new)
        while "iterator.begin().begin()" in new:
            new = new.replace("iterator.begin().begin()", "iterator.begin()")
        if new != c:
            f.write_text(new, encoding="utf-8")
            changed = True
    return changed


def apply_fix_missing_include_climits():
    """Adiciona #include <climits> em arquivos que usam INT_MAX/LLONG_MAX sem declarar."""
    changed = False
    tokens = ("INT_MAX", "INT_MIN", "LLONG_MAX", "LLONG_MIN", "LONG_MAX", "LONG_MIN", "UINT_MAX", "ULLONG_MAX")
    for f in SRC.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in {".h", ".cpp", ".hpp"}:
            continue
        try:
            c = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "<climits>" in c or "<limits.h>" in c:
            continue
        if not any(tok in c for tok in tokens):
            continue
        # insere depois do primeiro #include ou depois do header-guard/defines
        new = re.sub(r"(#include [<\"][^>\"]+[>\"])", r"\1\n#include <climits>", c, count=1)
        if new != c:
            f.write_text(new, encoding="utf-8")
            changed = True
    return changed


FIX_HANDLERS = {
    "soh_bytes": apply_fix_soh_bytes,
    "io_service_header": apply_fix_io_service_header,
    "io_service_typedef": apply_fix_io_service_typedef,
    "resolver_iterator": apply_fix_resolver_iterator,
    "resolver_handler_signature": apply_fix_resolver_handler_signature,
    "resolver_results_deref": apply_fix_resolver_results_deref,
    "timer_cancel_args": apply_fix_timer_cancel,
    "missing_include_string": apply_fix_missing_include_string,
    "missing_include_climits": apply_fix_missing_include_climits,
}


def main():
    log_path = sys.argv[1]
    log = Path(log_path).read_text(encoding="utf-8", errors="replace")
    tags = classify_errors(log)

    if not tags:
        print("Nenhuma tag de erro identificada no log.")
        return 2

    fingerprint = ",".join(sorted(tags))
    state = load_state()
    if state.get("last_fingerprint") == fingerprint:
        print(f"Mesmo fingerprint que iter anterior: {fingerprint}")
        return 3

    applied = []
    for t in sorted(tags):
        handler = FIX_HANDLERS.get(t)
        if not handler:
            print(f"  [skip] sem fix automatico pra: {t}")
            continue
        try:
            ok = handler()
            if ok:
                applied.append(t)
                print(f"  [ok] aplicou fix: {t}")
            else:
                print(f"  [noop] {t}: fix nao produziu diff")
        except Exception as e:
            print(f"  [err] {t}: {e}")

    state["last_fingerprint"] = fingerprint
    state["iters"] = state.get("iters", 0) + 1
    save_state(state)

    if not applied:
        print(f"Classifiquei tags mas nenhum fix bateu: {fingerprint}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
