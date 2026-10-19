"""MEMORY-PATTERNS — learning patterns: state machine + caps.

FIX MEMORY-V1 (2026-09-30):
  • state machine: candidate → active → shadow → retired → quarantined
  • active caps (100 global / 30 provider / 20 project)
  • evidence log (pattern_evidence)
  • retirement (auto по правилам)
  • API для мозга: get_for_prompt() — топ-N активных

Состояния:
  candidate  — только предложен (LLM или вручную), не влияет
  active     — проверен, влияет на оркестратор
  shadow     — под наблюдением, не влияет
  retired    — устарел, не влияет
  quarantined — сломан/опасен, не влияет

Правила:
  candidate → active:     evidence >= 2, confidence >= 0.6
  active → shadow:        conf < 0.4 ИЛИ 5 провалов подряд ИЛИ 60 дней
  shadow → retired:       90 дней ИЛИ перекрыт новым
  любой → quarantined:    вручную (баг/опасность)
"""
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.ai_link_db import get_db_path, init_schema


# ─────────────────────────────────────────────────────────────
#  Caps (Perplexity, MEMORY-V1)
# ─────────────────────────────────────────────────────────────

CAP_GLOBAL = 100
CAP_PROVIDER = 30
CAP_PROJECT = 20
CAP_PROMPT = 10

STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"
STATUS_SHADOW = "shadow"
STATUS_RETIRED = "retired"
STATUS_QUARANTINED = "quarantined"

VALID_STATUSES = {
    STATUS_CANDIDATE, STATUS_ACTIVE, STATUS_SHADOW,
    STATUS_RETIRED, STATUS_QUARANTINED,
}


# ─────────────────────────────────────────────────────────────
#  helpers
# ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _con(db_path: Optional[Path]) -> sqlite3.Connection:
    path = init_schema(db_path)
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "signature": row[1],
        "context_json": row[2] or "",
        "fix": row[3] or "",
        "success_count": row[4] or 0,
        "failure_count": row[5] or 0,
        "confidence": row[6] or 0.0,
        "updated_at": row[7] or "",
        "status": row[8] or STATUS_CANDIDATE,
        "scope": row[9] or "global",
        "scope_value": row[10] or "",
        "source": row[11] or "observed",
        "evidence_count": row[12] or 0,
        "salience": row[13] if row[13] is not None else 0.5,
        "last_used_at": row[14] or "",
    }


# ─────────────────────────────────────────────────────────────
#  add_pattern
# ─────────────────────────────────────────────────────────────

def add_pattern(
    signature: str,
    context_json: str = "",
    fix: str = "",
    scope: str = "global",
    scope_value: str = "",
    source: str = "observed",
    salience: float = 0.5,
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Создаёт паттерн в состоянии candidate. Возвращает id или None.

    Если паттерн с таким signature уже есть — возвращает его id
    (не пересоздаёт, не перезаписывает контекст).
    """
    if not signature or not signature.strip():
        return None
    con = _con(db_path)
    try:
        cur = con.execute(
            "SELECT id FROM learning_patterns WHERE signature = ?",
            (signature.strip(),),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        now = _now()
        # CONFIDENCE-PRIOR (2026-10-11): Beta(1,1) prior mean = 0.5.
        # Раньше confidence жёстко = 0.0 при создании, потом
        # обновлялось только при record_evidence. Паттерны без
        # evidence имели conf=0.0, что странно: (alpha+S)/(alpha+beta+S+F)
        # при alpha=beta=1, S=F=0 даёт 0.5.
        _prior_conf = 1.0 / (1.0 + 1.0)  # = 0.5
        cur = con.execute(
            "INSERT INTO learning_patterns "
            "(signature, context_json, fix, success_count, failure_count, "
            " confidence, updated_at, status, scope, scope_value, source, "
            " evidence_count, salience) "
            "VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, 0, ?)",
            (signature.strip(), context_json, fix, _prior_conf, now,
             STATUS_CANDIDATE, scope, scope_value, source, salience),
        )
        con.commit()
        return cur.lastrowid
    except Exception:
        return None
    finally:
        con.close()


def get_pattern(pattern_id: int,
                db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    con = _con(db_path)
    try:
        cur = con.execute(
            "SELECT id, signature, context_json, fix, success_count, "
            "       failure_count, confidence, updated_at, status, scope, "
            "       scope_value, source, evidence_count, salience, last_used_at "
            "FROM learning_patterns WHERE id = ?",
            (pattern_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        con.close()


def get_by_signature(signature: str,
                     db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    con = _con(db_path)
    try:
        cur = con.execute(
            "SELECT id, signature, context_json, fix, success_count, "
            "       failure_count, confidence, updated_at, status, scope, "
            "       scope_value, source, evidence_count, salience, last_used_at "
            "FROM learning_patterns WHERE signature = ?",
            (signature,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  evidence
# ─────────────────────────────────────────────────────────────

def _count_consecutive_unused(pattern_id: int,
                              db_path: Optional[Path] = None) -> int:
    """B+ (19.10.2026): сколько раз ПОДРЯД retrieved=1, used=0.

    Для авто-понижения (1.1.6): если 5 раз подряд паттерн
    вставлялся в промпт, но модель его не использовала —
    он бесполезен → shadow.
    """
    con = _con(db_path)
    try:
        # Берём последние evidence в обратном порядке
        cur = con.execute(
            "SELECT retrieved, used FROM pattern_evidence "
            "WHERE pattern_id = ? ORDER BY rowid DESC LIMIT 20",
            (pattern_id,),
        )
        rows = cur.fetchall()
        count = 0
        for r in rows:
            retrieved = r[0] if r[0] is not None else 0
            used = r[1] if r[1] is not None else 0
            if retrieved and not used:
                count += 1
            else:
                break
        return count
    except Exception:
        return 0
    finally:
        con.close()


def record_evidence(
    pattern_id: int,
    outcome: str,                 # "success" | "failure" | "neutral"
    run_id: str = "",
    model: str = "",
    injected: bool = False,
    verification_json: str = "",
    # FIX ATTRIBUTION (2026-09-30): 5-уровневая модель
    phase: str = "",
    problem_keywords: str = "",
    relevance_score: float = 0.0,
    retrieved: bool = False,
    eligible: bool = False,
    used: bool = False,
    supported: bool = False,
    verified_effect: bool = False,
    attribution_method: str = "",
    db_path: Optional[Path] = None,
) -> bool:
    """Записывает доказательство применения паттерна.

    FIX ATTRIBUTION (2026-09-30): Beta posterior вместо success/(s+f).
      confidence = (alpha + S) / (alpha + beta + S + F)

    Веса по 5-уровневой модели:
      verified_effect=True → S += 1.0 (полный credit)
      used+supported=True  → S += 0.25 (частичный credit)
      иначе               → не обновляет confidence

    Backward-compat: старые вызовы без attribution → verified_effect=True
    для success/failure (legacy-режим, эквивалент старой семантики).
    """
    if outcome not in ("success", "failure", "neutral"):
        return False

    # Backward-compat
    _attr_given = bool(
        retrieved or eligible or used or supported
        or verified_effect or attribution_method
    )
    # FIX 1.1.1 (17.10.2026): legacy ТОЛЬКО для success.
    # Для failure verified_effect=False по определению:
    # фаза провалилась → verify_passed=False → verified=False.
    # Legacy для failure приводил к weight_f=0.25 при used=False,
    # то есть штрафовал паттерн, который не применялся.
    if not _attr_given and outcome == "success":
        verified_effect = True
        attribution_method = "legacy"

    con = _con(db_path)
    try:
        ev_id = str(uuid.uuid4())
        con.execute(
            "INSERT INTO pattern_evidence "
            "(id, pattern_id, run_id, model, injected, outcome, "
            " verification_json, created_at, phase, problem_keywords, "
            " relevance_score, retrieved, eligible, used, supported, "
            " verified_effect, attribution_method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ev_id, pattern_id, run_id, model,
                1 if injected else 0, outcome,
                verification_json or "", _now(),
                phase or "",
                problem_keywords or "",
                float(relevance_score or 0.0),
                1 if retrieved else 0,
                1 if eligible else 0,
                1 if used else 0,
                1 if supported else 0,
                1 if verified_effect else 0,
                attribution_method or "",
            ),
        )

        # Веса для Beta
        weight_s = 0.0
        weight_f = 0.0
        if outcome == "success":
            if verified_effect:
                weight_s = 1.0
            elif used and supported:
                weight_s = 0.25
        elif outcome == "failure":
            # FIX 1.1.1 (17.10.2026): штрафуем только если паттерн
            # реально применялся (used=True). Иначе не его вина:
            # паттерн лежал в промпте, но модель его не использовала.
            if used:
                weight_f = 0.5      # применили → не помогло → штраф
            elif verified_effect:
                weight_f = 0.25     # редкий legacy-случай
            else:
                # B+ (19.10.2026): мягкий штраф 0.1 при used=False.
                # Раньше 0.0 — export (0 success, 2 failure) оставался
                # 0.5, не уходил в shadow. Теперь падает.
                weight_f = 0.1      # не применён → мягкий штраф (было 0.0)

        con.execute(
            "UPDATE learning_patterns "
            "SET success_count = success_count + ?, "
            "    failure_count = failure_count + ?, "
            "    evidence_count = evidence_count + 1, "
            "    alpha = alpha + ?, "
            "    beta = beta + ?, "
            "    last_used_at = ?, "
            "    updated_at = ? "
            "WHERE id = ?",
            (
                1 if outcome == "success" else 0,
                1 if outcome == "failure" else 0,
                weight_s, weight_f,
                _now(), _now(),
                pattern_id,
            ),
        )

        # Beta posterior
        cur = con.execute(
            "SELECT alpha, beta FROM learning_patterns WHERE id = ?",
            (pattern_id,),
        )
        row = cur.fetchone()
        if row:
            a = row[0] if row[0] is not None else 1.0
            b = row[1] if row[1] is not None else 1.0
            conf = (a / (a + b)) if (a + b) > 0 else 0.5
            con.execute(
                "UPDATE learning_patterns SET confidence = ? WHERE id = ?",
                (conf, pattern_id),
            )
        con.commit()

        # B+ (19.10.2026): авто-понижение (1.1.6).
        # 5 раз подряд retrieved=1, used=0 → shadow.
        try:
            if retrieved and not used:
                _unused = _count_consecutive_unused(
                    pattern_id, db_path
                )
                # B+ (19.10.2026): порог 10 (было 5). 5 — слишком
                # агрессивно: test-models (86 evidence, used=11)
                # ушёл в shadow после 5 unused подряд. 10 — баланс.
                if _unused >= 10:
                    _set_status(pattern_id, STATUS_SHADOW, db_path)
                    import logging as _lg
                    _lg.getLogger(__name__).warning(
                        "AUTO-DEMOTE: паттерн %d — %d раз "
                        "retrieved=1, used=0 → shadow",
                        pattern_id, _unused,
                    )
        except Exception:
            pass

        return True
    except Exception:
        return False
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  state transitions
# ─────────────────────────────────────────────────────────────

def _set_status(pattern_id: int, new_status: str,
                db_path: Optional[Path]) -> bool:
    if new_status not in VALID_STATUSES:
        return False
    con = _con(db_path)
    try:
        con.execute(
            "UPDATE learning_patterns SET status = ?, updated_at = ? "
            "WHERE id = ?",
            (new_status, _now(), pattern_id),
        )
        con.commit()
        return True
    finally:
        con.close()


def promote(pattern_id: int,
            db_path: Optional[Path] = None) -> bool:
    """candidate → active, shadow → active.

    Условие auto: evidence >= 2 и confidence >= 0.6.
    Если вызван вручную — переводит без проверки.
    """
    p = get_pattern(pattern_id, db_path)
    if not p:
        return False
    if p["status"] not in (STATUS_CANDIDATE, STATUS_SHADOW):
        return False
    return _set_status(pattern_id, STATUS_ACTIVE, db_path)


def promote_if_ready(pattern_id: int,
                     db_path: Optional[Path] = None) -> bool:
    """candidate → active ТОЛЬКО если evidence >= 2 и conf >= 0.6."""
    p = get_pattern(pattern_id, db_path)
    if not p:
        return False
    if p["status"] != STATUS_CANDIDATE:
        return False
    if p["evidence_count"] < 2 or p["confidence"] < 0.6:
        return False
    return _set_status(pattern_id, STATUS_ACTIVE, db_path)


def demote(pattern_id: int,
           db_path: Optional[Path] = None) -> bool:
    """active → shadow. Для ручного перевода или auto_retire."""
    p = get_pattern(pattern_id, db_path)
    if not p or p["status"] != STATUS_ACTIVE:
        return False
    return _set_status(pattern_id, STATUS_SHADOW, db_path)


def retire(pattern_id: int,
           db_path: Optional[Path] = None) -> bool:
    """shadow → retired."""
    p = get_pattern(pattern_id, db_path)
    if not p or p["status"] not in (STATUS_SHADOW, STATUS_ACTIVE):
        return False
    return _set_status(pattern_id, STATUS_RETIRED, db_path)


def quarantine(pattern_id: int,
               db_path: Optional[Path] = None) -> bool:
    """любой → quarantined (баг/опасность)."""
    p = get_pattern(pattern_id, db_path)
    if not p:
        return False
    return _set_status(pattern_id, STATUS_QUARANTINED, db_path)


# ─────────────────────────────────────────────────────────────
#  caps
# ─────────────────────────────────────────────────────────────

def count_by_status(status: str,
                    scope: str = "",
                    db_path: Optional[Path] = None) -> int:
    con = _con(db_path)
    try:
        sql = "SELECT COUNT(*) FROM learning_patterns WHERE status = ?"
        params: List[Any] = [status]
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        cur = con.execute(sql, params)
        return cur.fetchone()[0]
    finally:
        con.close()


def enforce_caps(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Понижает лишние active → shadow.

    Caps: global=100, provider=30, project=20.
    Оставляет самые «уверенные» (по confidence), остальные — shadow.
    Возвращает {"demoted": N}.
    """
    demoted = 0
    con = _con(db_path)
    try:
        # 1. Global cap
        cur = con.execute(
            "SELECT id FROM learning_patterns "
            "WHERE status = ? AND scope = 'global' "
            "ORDER BY confidence DESC, success_count DESC",
            (STATUS_ACTIVE,),
        )
        ids = [r[0] for r in cur.fetchall()]
        if len(ids) > CAP_GLOBAL:
            for pid in ids[CAP_GLOBAL:]:
                con.execute(
                    "UPDATE learning_patterns SET status = ?, updated_at = ? "
                    "WHERE id = ?",
                    (STATUS_SHADOW, _now(), pid),
                )
                demoted += 1

        # 2. Per scope_value (provider / project)
        cur = con.execute(
            "SELECT DISTINCT scope, scope_value FROM learning_patterns "
            "WHERE status = ? AND scope != 'global'",
            (STATUS_ACTIVE,),
        )
        buckets = cur.fetchall()
        for scope, sv in buckets:
            cap = CAP_PROVIDER if scope == "provider" else CAP_PROJECT
            cur2 = con.execute(
                "SELECT id FROM learning_patterns "
                "WHERE status = ? AND scope = ? AND scope_value = ? "
                "ORDER BY confidence DESC, success_count DESC",
                (STATUS_ACTIVE, scope, sv),
            )
            bucket_ids = [r[0] for r in cur2.fetchall()]
            if len(bucket_ids) > cap:
                for pid in bucket_ids[cap:]:
                    con.execute(
                        "UPDATE learning_patterns "
                        "SET status = ?, updated_at = ? WHERE id = ?",
                        (STATUS_SHADOW, _now(), pid),
                    )
                    demoted += 1

        con.commit()
        return {"demoted": demoted}
    except Exception:
        return {"demoted": 0}
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  retirement (auto)
# ─────────────────────────────────────────────────────────────

def auto_retire(days_shadow: int = 60,
                days_retired: int = 90,
                min_confidence: float = 0.4,
                max_failures: int = 5,
                db_path: Optional[Path] = None) -> Dict[str, int]:
    """Автоматическое понижение статусов.

    active  → shadow:  conf < 0.4 ИЛИ failures >= 5
    shadow  → retired: last_used_at > 90 дней назад
    """
    to_shadow = 0
    to_retired = 0
    now = datetime.now()
    cutoff_shadow = (now - timedelta(days=days_shadow)).isoformat()
    cutoff_retired = (now - timedelta(days=days_retired)).isoformat()

    con = _con(db_path)
    try:
        # active → shadow по confidence / failure
        cur = con.execute(
            "SELECT id, confidence, failure_count FROM learning_patterns "
            "WHERE status = ?",
            (STATUS_ACTIVE,),
        )
        for pid, conf, fail in cur.fetchall():
            c = conf or 0.0
            f = fail or 0
            if c < min_confidence or f >= max_failures:
                con.execute(
                    "UPDATE learning_patterns SET status = ?, updated_at = ? "
                    "WHERE id = ?",
                    (STATUS_SHADOW, _now(), pid),
                )
                to_shadow += 1

        # shadow → retired по давности
        cur = con.execute(
            "SELECT id, last_used_at, updated_at FROM learning_patterns "
            "WHERE status = ?",
            (STATUS_SHADOW,),
        )
        for pid, last_used, updated in cur.fetchall():
            ref = last_used or updated or ""
            if ref and ref < cutoff_retired:
                con.execute(
                    "UPDATE learning_patterns SET status = ?, updated_at = ? "
                    "WHERE id = ?",
                    (STATUS_RETIRED, _now(), pid),
                )
                to_retired += 1

        con.commit()
        return {"to_shadow": to_shadow, "to_retired": to_retired}
    except Exception:
        return {"to_shadow": 0, "to_retired": 0}
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  get_for_prompt — топ-N активных
# ─────────────────────────────────────────────────────────────

def get_for_prompt(
    scope: Optional[str] = None,
    project_name: str = "",
    limit: int = CAP_PROMPT,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Возвращает топ-N активных паттернов (для инъекции в prompt).

    Сортировка: salience DESC, confidence DESC.
    limit по умолчанию = CAP_PROMPT (10).
    """
    con = _con(db_path)
    try:
        sql = (
            "SELECT id, signature, context_json, fix, success_count, "
            "       failure_count, confidence, updated_at, status, scope, "
            "       scope_value, source, evidence_count, salience, last_used_at "
            "FROM learning_patterns WHERE status = ? "
        )
        params: List[Any] = [STATUS_ACTIVE]
        # MEMORY-PROJECT-SCOPE (2026-10-11):
        #   • project_name задан → global + project:<имя>
        #   • scope задан (старое поведение) → точный фильтр
        #   • ничего не задано → все активные (обратная совместимость)
        _proj = (project_name or "").strip()
        if _proj:
            sql += ("AND (scope = 'global' OR "
                    "(scope = 'project' AND scope_value = ?)) ")
            params.append(_proj)
        elif scope:
            sql += "AND scope = ? "
            params.append(scope)
        sql += "ORDER BY salience DESC, confidence DESC LIMIT ?"
        params.append(int(limit))
        cur = con.execute(sql, params)
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def stats(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Сводка по статусам."""
    out: Dict[str, int] = {}
    con = _con(db_path)
    try:
        for st in VALID_STATUSES:
            cur = con.execute(
                "SELECT COUNT(*) FROM learning_patterns WHERE status = ?",
                (st,),
            )
            out[st] = cur.fetchone()[0]
        cur = con.execute("SELECT COUNT(*) FROM pattern_evidence")
        out["evidence_total"] = cur.fetchone()[0]

        # B+ (19.10.2026): метрики эффективности памяти.
        # hit_rate = verified_effect / retrieved (цель: 15%).
        # useful_hit_rate = used / retrieved.
        try:
            cur = con.execute(
                "SELECT "
                "COALESCE(SUM(retrieved), 0), "
                "COALESCE(SUM(used), 0), "
                "COALESCE(SUM(verified_effect), 0) "
                "FROM pattern_evidence"
            )
            _ret, _used, _ver = cur.fetchone()
            out["retrieved_total"] = int(_ret or 0)
            out["used_total"] = int(_used or 0)
            out["verified_total"] = int(_ver or 0)
            out["hit_rate"] = (
                round(_ver / _ret, 3) if _ret else 0.0
            )
            out["useful_hit_rate"] = (
                round(_used / _ret, 3) if _ret else 0.0
            )
            # B+ (19.10.2026): score (0-100) для UI.
            # hit_rate*50 + useful*30 + active_ratio*20.
            _act = out.get(STATUS_ACTIVE, 0) or 0
            _cand = out.get(STATUS_CANDIDATE, 0) or 0
            _ratio = (_act / (_act + _cand)) if (_act + _cand) else 0.0
            _score = (
                out["hit_rate"] * 50.0
                + out["useful_hit_rate"] * 30.0
                + _ratio * 20.0
            )
            out["score"] = round(_score, 1)
        except Exception:
            pass

        # stale_rate: active без использования > 60 дней
        try:
            from datetime import datetime as _dt, timedelta as _td
            _cutoff = (
                _dt.now() - _td(days=60)
            ).isoformat(timespec="seconds")
            cur = con.execute(
                "SELECT COUNT(*) FROM learning_patterns "
                "WHERE status = ? AND (last_used_at IS NULL "
                "OR last_used_at < ?)",
                (STATUS_ACTIVE, _cutoff),
            )
            _stale = cur.fetchone()[0]
            _active = out.get(STATUS_ACTIVE, 0) or 0
            out["active_stale"] = int(_stale)
            out["stale_rate"] = (
                round(_stale / _active, 3) if _active else 0.0
            )
        except Exception:
            pass

        return out
    finally:
        con.close()
