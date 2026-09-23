#!/usr/bin/env python3
"""
Generate a redirect map from a list of old URLs to a list of new URLs,
matching each old URL to the most similar new URL.

Usage:
    python url_redirects.py old_urls.txt new_urls.txt -o redirects.csv
    python url_redirects.py old.txt new.txt --format htaccess -o .htaccess
    python url_redirects.py old.txt new.txt --format nginx --threshold 0.5

Input files: one URL (or path) per line. Blank lines and lines starting with # are ignored.
"""

import argparse
import csv
import re
import sys
from difflib import SequenceMatcher
from urllib.parse import urlparse, unquote

STOPWORDS = {"the", "a", "an", "and", "of", "to", "in", "for", "on", "with", "index", "html", "htm", "php", "aspx", "asp"}


def read_urls(path):
    with open(path, encoding="utf-8") as f:
        seen, urls = set(), []
        for line in f:
            # Drop query strings and fragments so variants like ?ical=1 merge into one URL.
            line = line.strip().split("#", 1)[0].split("?", 1)[0] if not line.lstrip().startswith("#") else ""
            if line and line not in seen:
                seen.add(line)
                urls.append(line)
        return urls


def normalise_path(url):
    """Return a lowercase path without domain, query, trailing slash or file extension."""
    path = unquote(urlparse(url if "://" in url else "http://x" + ("" if url.startswith("/") else "/") + url).path)
    path = path.lower().rstrip("/") or "/"
    path = re.sub(r"/index\.(html?|php|aspx?)$", "", path) or "/"
    path = re.sub(r"\.(html?|php|aspx?)$", "", path)
    return path


def tokens(path):
    return [t for t in re.split(r"[^a-z0-9]+", path) if t and t not in STOPWORDS]


def similarity(old, new):
    """Blend of slug similarity, token overlap and full-path similarity (0..1)."""
    old_slug, new_slug = old.rsplit("/", 1)[-1], new.rsplit("/", 1)[-1]
    slug_score = SequenceMatcher(None, old_slug, new_slug).ratio()

    old_tok, new_tok = set(tokens(old)), set(tokens(new))
    union = old_tok | new_tok
    token_score = len(old_tok & new_tok) / len(union) if union else 0.0

    path_score = SequenceMatcher(None, old, new).ratio()

    return 0.45 * slug_score + 0.35 * token_score + 0.20 * path_score


def build_redirects(old_urls, new_urls, threshold):
    new_norm = {u: normalise_path(u) for u in new_urls}
    norm_to_new = {n: u for u, n in new_norm.items()}

    results = []
    for old in old_urls:
        old_n = normalise_path(old)

        # Identical path on the new site: nothing to redirect.
        if old_n in norm_to_new:
            results.append((old, norm_to_new[old_n], 1.0, "exact"))
            continue

        best_url, best_score = None, 0.0
        for new, new_n in new_norm.items():
            score = similarity(old_n, new_n)
            if score > best_score:
                best_url, best_score = new, score

        status = "match" if best_score >= threshold else "low_confidence"
        results.append((old, best_url, round(best_score, 3), status))
    return results


def to_path(url):
    p = urlparse(url)
    return (p.path or "/") if p.scheme else url


def write_output(results, fmt, out, include_low, fallback):
    rows = []
    for old, new, score, status in results:
        if status == "exact":
            continue
        # CSV is for review, so it always keeps unmatched URLs, with the target left blank.
        if status == "low_confidence" and not include_low:
            if fmt == "csv":
                rows.append((to_path(old), "", score, status))
                continue
            if not fallback:
                continue
            new = fallback
        rows.append((to_path(old), to_path(new), score, status))

    if fmt == "csv":
        w = csv.writer(out)
        w.writerow(["old_path", "new_path", "score", "status"])
        w.writerows(rows)
    elif fmt == "htaccess":
        for old, new, score, status in rows:
            out.write(f"Redirect 301 {old} {new}  # {score} {status}\n")
    elif fmt == "nginx":
        for old, new, score, status in rows:
            out.write(f"location = {old} {{ return 301 {new}; }}  # {score} {status}\n")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Create a redirect file by matching old URLs to the most similar new URLs.")
    ap.add_argument("old", help="file with old URLs, one per line")
    ap.add_argument("new", help="file with new URLs, one per line")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("-f", "--format", choices=["csv", "htaccess", "nginx"], default="csv")
    ap.add_argument("-t", "--threshold", type=float, default=0.4, help="minimum score to accept a match (0-1, default 0.4)")
    ap.add_argument("--include-low", action="store_true", help="include best-guess matches below the threshold (CSV otherwise lists them with a blank target)")
    ap.add_argument("--fallback", help="htaccess/nginx: redirect below-threshold URLs here instead (e.g. / )")
    args = ap.parse_args()

    old_urls, new_urls = read_urls(args.old), read_urls(args.new)
    if not new_urls:
        sys.exit("No new URLs found.")

    results = build_redirects(old_urls, new_urls, args.threshold)

    out = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
    try:
        written = write_output(results, args.format, out, args.include_low, args.fallback)
    finally:
        if args.output:
            out.close()

    counts = {s: sum(1 for r in results if r[3] == s) for s in ("exact", "match", "low_confidence")}
    print(
        f"{len(old_urls)} old URLs: {counts['exact']} unchanged, {counts['match']} matched, "
        f"{counts['low_confidence']} below threshold. {written} redirects written.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
