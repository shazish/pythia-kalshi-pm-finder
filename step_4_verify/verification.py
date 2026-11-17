"""Evidence capture and fail-closed verification; automated review is orchestrated separately."""
import hashlib
import ipaddress
import json
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urljoin

REPO = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO / "source_policy.json"
VERSION = 2  # 2: source tiers, ignored/fetch_failed checks, needs_review/downgraded statuses


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def fresh(timestamp, hours):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
        return 0 <= age < hours * 3600
    except (ValueError, TypeError):
        return False


def load_policy(path=POLICY_PATH):
    policy = json.loads(Path(path).read_text())
    for key in ("trusted_domains", "blocked_domains"):
        domains = policy.get(key)
        if not isinstance(domains, list) or any(not isinstance(d, str) or
                d != d.lower() or "/" in d or not d or "*" in d for d in domains):
            raise ValueError(f"Invalid domain list: {key}")
    mapping = policy.get("resolution_domains_by_ticker")
    if not isinstance(mapping, dict):
        raise ValueError("resolution_domains_by_ticker must be a mapping")
    for domains in mapping.values():
        if not isinstance(domains, list) or any(not isinstance(d, str) or not d or
                "/" in d or "*" in d or d != d.lower() for d in domains):
            raise ValueError("Invalid resolution domain list")
    for key in ("evidence_ttl_hours", "verification_ttl_hours"):
        if not isinstance(policy.get(key), (int, float)) or not 0 < policy[key] <= 168:
            raise ValueError(f"{key} must be between 0 and 168")
    policy.setdefault("min_verified_signals", 3)
    if type(policy["min_verified_signals"]) is not int or not 1 <= policy["min_verified_signals"] <= 10:
        raise ValueError("min_verified_signals must be an integer between 1 and 10")
    return policy


def hostname(url):
    if not isinstance(url, str):
        raise ValueError("URL must be text")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A complete HTTPS URL without credentials is required")
    if parsed.port not in (None, 443):
        raise ValueError("Only HTTPS port 443 is allowed")
    return parsed.hostname.lower().rstrip(".")


def matches(host, domain):
    return host == domain or host.endswith("." + domain)


def source_category(url, ticker, policy):
    host = hostname(url)
    if any(matches(host, d) for d in policy["blocked_domains"]):
        return "blocked"
    if any(matches(host, d) for d in policy["resolution_domains_by_ticker"].get(ticker, [])):
        return "resolution"
    if any(matches(host, d) for d in policy["trusted_domains"]):
        return "trusted"
    return "unknown"


# Source weight for a verified claim. Any unlisted, non-blocked publisher is semi-reliable:
# usable as support, but the settlement-deciding fact needs a stronger or second source.
TIER_RANK = {"resolution": 3, "trusted": 2, "unknown": 1}
TIER_NAME = {"resolution": "resolution", "trusted": "tier1", "unknown": "semi"}
SECOND_LEVEL_LABELS = ("co", "com", "gov", "org", "ac", "net", "govt", "go", "or", "edu")
DUPLICATE_EXCERPT_RATIO = 0.8


def registrable_domain(host):
    """Approximate publisher identity (no public-suffix list dependency)."""
    parts = host.lower().rstrip(".").split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL_LABELS and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def independent(first, second):
    """Two verified checks are independent if different publishers wrote different text."""
    from difflib import SequenceMatcher
    if registrable_domain(hostname(first["evidence"]["final_url"])) == \
            registrable_domain(hostname(second["evidence"]["final_url"])):
        return False
    a, b = (normalize(c["review"].get("excerpt", "")).lower() for c in (first, second))
    return SequenceMatcher(None, a, b).ratio() < DUPLICATE_EXCERPT_RATIO


def sourcing_decision(checks, policy):
    """Status and reason for an entry whose evidence content has fully passed review."""
    counted = [c for c in checks if c["status"] == "verified"]
    minimum = policy["min_verified_signals"]
    if len(counted) < minimum:
        return "downgraded", (f"Only {len(counted)} confirming signal(s) verified after excluding "
                              f"blocked sources; CERTAIN needs {minimum}")
    decisive = [c for c in counted if c.get("decisive")]
    if any(c["tier"] in ("resolution", "tier1") for c in decisive):
        return "verified", "Deciding fact confirmed by a settlement or Tier 1 source"
    semi = [c for c in decisive if c["tier"] == "semi"]
    if any(independent(a, b) for i, a in enumerate(semi) for b in semi[i + 1:]):
        return "verified", "Deciding fact confirmed by two independent semi-reliable sources"
    if semi:
        return "downgraded", "Deciding fact rests on a single semi-reliable source"
    return "downgraded", "No verified source establishes the deciding fact on its own"


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def normalize(text):
    return " ".join(text.split())


def fetch_page(url):
    """Bounded public HTTPS retrieval. Validate every redirect before following."""
    import requests
    current = url
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(6):
            host = hostname(current)
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("Non-public source address")
            with session.get(current, timeout=(5, 15), allow_redirects=False, stream=True,
                             headers={"User-Agent": "KalshiEvidenceVerifier/1.0"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    current = urljoin(current, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml+xml")):
                    raise ValueError("Unsupported content type; no readable article captured")
                chunks, size = [], 0
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError("Page exceeds evidence size limit")
                    chunks.append(chunk)
                raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                if "html" in content_type:
                    parser = PageText()
                    parser.feed(raw)
                    raw = " ".join(parser.parts)
                text = normalize(raw)
                if len(text) < 80:
                    raise ValueError("Insufficient readable source text")
                return {"final_url": current, "text": text, "http_status": response.status_code}
    raise ValueError("Too many redirects")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temporary file avoids collisions between overlapping writers.
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as output:
        json.dump(value, output, indent=2)
        temporary = output.name
    os.replace(temporary, path)


class EvidenceCache:
    def __init__(self, directory, policy, fetcher=fetch_page):
        self.directory = Path(directory)
        self.policy = policy
        self.fetcher = fetcher
        self.memo = {}

    def get(self, url):
        if url in self.memo:
            return self.memo[url]
        path = self.directory / (digest(url) + ".json")
        try:
            cached = json.loads(path.read_text())
            if (cached["url"] == url and cached.get("status") == "captured"
                    and cached["content_hash"] == digest(cached["text"])
                    and fresh(cached["retrieved_at"], self.policy["evidence_ttl_hours"])):
                self.memo[url] = cached
                return cached
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            hostname(url)
            page = self.fetcher(url)
            hostname(page["final_url"])
            record = {**page, "url": url, "retrieved_at": now(),
                      "content_hash": digest(page["text"]), "status": "captured"}
            atomic_json(path, record)
        except Exception as error:
            record = {"url": url, "status": "unverifiable", "error": str(error)[:300],
                      "retrieved_at": now()}
        self.memo[url] = record
        return record


def split_entry(entry):
    if isinstance(entry.get("classification"), dict):
        return entry.get("candidate", {}), entry["classification"]
    return entry, entry


def input_hash(candidate, classification):
    # Exclude validation metadata: finalization legitimately recomputes it.
    candidate = {k: v for k, v in candidate.items() if not k.startswith("_")
                 and k not in ("research", "classification", "reasons", "confirming_signals",
                               "contradicting_signals", "confidence_score", "searched_for",
                               "recent_developments", "what_would_change_this", "settlement_risk")}
    classification = {k: classification.get(k) for k in (
        "classification", "high_confidence_side", "confidence_score", "reasons",
        "confirming_signals", "contradicting_signals", "searched_for",
        "recent_developments", "what_would_change_this", "settlement_risk")}
    return digest({"candidate": candidate, "classification": classification})


def verify_entry(entry, research, policy, cache, reviews):
    candidate, classification = split_entry(entry)
    ticker = candidate.get("ticker", "")
    fingerprint = input_hash(candidate, classification)
    checks = []
    signals = classification.get("confirming_signals", [])
    findings = research.get("findings", []) if isinstance(research, dict) else []
    urls = {f.get("url") or f.get("source_url") for f in findings if isinstance(f, dict)}
    for signal in signals if isinstance(signals, list) else []:
        check = {"status": "unverifiable", "fact": "", "url": ""}
        checks.append(check)
        if not isinstance(signal, dict) or not isinstance(signal.get("fact"), str) or not signal["fact"].strip():
            check["reason"] = "Missing structured claim"
            continue
        url = signal.get("source_url", "")
        check.update(fact=signal["fact"], url=url)
        try:
            category = source_category(url, ticker, policy)
        except (ValueError, TypeError):
            check["reason"] = "Missing or invalid HTTPS citation"
            continue
        check["source_category"] = category
        if category == "blocked":
            # Not evidence (prediction market, AI page, aggregator...): ignored, not fatal.
            check.update(status="ignored", reason="Source excluded by policy; not counted as evidence")
            continue
        if url not in urls:
            check["reason"] = "Citation absent from this ticker's research"
            continue
        page = cache.get(url)
        check["evidence"] = {k: v for k, v in page.items() if k != "text"}
        check["evidence"]["cache_file"] = str(cache.directory / (digest(url) + ".json"))
        if page["status"] != "captured":
            # The page could not be read (403, PDF, timeout): the claim is untested, not refuted.
            check.update(status="fetch_failed", reason=f"Source could not be fetched: {page['error']}")
            continue
        final_category = source_category(page["final_url"], ticker, policy)
        if final_category == "blocked":
            check.update(status="ignored", reason="Redirected source excluded by policy; not counted as evidence")
            continue
        # Redirects cannot inherit the original publisher's status: use the weaker tier.
        check["tier"] = TIER_NAME[min(category, final_category, key=TIER_RANK.get)]
        review_id = digest({"input": fingerprint, "url": url, "fact": signal["fact"],
                            "content": page["content_hash"], "policy": digest(policy)})
        check["review_id"] = review_id
        review = reviews.get(review_id, {})
        if not isinstance(review, dict):
            review = {}
        if (review.get("automatic_version") and review.get("verdict") == "unverifiable"
                and fresh(review.get("reviewed_at"), policy["verification_ttl_hours"])):
            check["review"] = review
            check["reason"] = review.get("reason", "Automatic review could not establish support")
            continue
        excerpt = normalize(review.get("excerpt", "")) if isinstance(review.get("excerpt"), str) else ""
        if (not review.get("reviewer") or not fresh(review.get("reviewed_at"), policy["verification_ttl_hours"])
                or len(excerpt) < 20 or excerpt not in normalize(page["text"])):
            check["reason"] = "Needs a dated evidence review with an exact supporting excerpt"
            continue
        check["review"] = review
        if review.get("verdict") == "contradicted":
            check.update(status="contradicted", reason=review.get("reason", "Evidence contradicts claim"))
        elif (review.get("verdict") == "supported" and review.get("settlement_match") is True
              and review.get("time_relevant") is True and review.get("article_accessible") is True
              and review.get("reason") and candidate.get("rules_primary")):
            check.update(status="verified", reason=review["reason"], decisive=review.get("decisive") is True)
        else:
            check["reason"] = "Review does not establish support, readable article, timing and settlement match"

    # Precedence: refuted > failed content check (withheld) > unreadable source (needs review)
    # > sourcing strength (verified, or downgraded to LIKELY).
    statuses = {c["status"] for c in checks}
    reason = ""
    if not checks:
        status, reason = "unverifiable", "No confirming signals"
    elif "contradicted" in statuses:
        status = "contradicted"
    elif "unverifiable" in statuses:
        status = "unverifiable"
    elif "fetch_failed" in statuses:
        status, reason = "needs_review", "One or more sources could not be fetched; check them manually"
    else:
        status, reason = sourcing_decision(checks, policy)
    report = {"version": VERSION, "status": status, "reason": reason, "input_hash": fingerprint,
              "policy_hash": digest(policy), "checked_at": now(), "checks": checks}
    classification["_verification"] = report
    return report


def verification_passes(candidate, classification, policy=None):
    """Shared finalization gate: absent, expired or changed evidence never passes."""
    try:
        policy = policy or load_policy()
        report = classification.get("_verification", {})
        if not (report.get("version") == VERSION and report.get("status") == "verified"
                and report.get("input_hash") == input_hash(candidate, classification)
                and report.get("policy_hash") == digest(policy)
                and fresh(report.get("checked_at"), policy["verification_ttl_hours"])
                and report.get("checks")):
            return False
        for check in report["checks"]:
            if check.get("status") == "ignored":
                continue
            if (check.get("status") != "verified"
                    or not fresh(check["evidence"]["retrieved_at"], policy["evidence_ttl_hours"])
                    or not fresh(check["review"]["reviewed_at"], policy["verification_ttl_hours"])):
                return False
        return True
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        return False
