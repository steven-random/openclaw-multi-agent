#!/usr/bin/env python3
"""
email_ops.py — Yahoo Mail CLI tool for OpenClaw Email Agent
Usage: python email_ops.py <command> [options]

Credentials read from environment variables:
  EMAIL_ADDRESS, EMAIL_APP_PASSWORD, IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT
"""
import os, sys, imaplib, smtplib, email, argparse, re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr
from html import unescape
from pathlib import Path

# ── Credentials ────────────────────────────────────────────────────────────
# Auto-load .env from the same directory as this script
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

EMAIL_ADDR  = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_PASS  = os.environ.get("EMAIL_APP_PASSWORD", "")
IMAP_HOST   = os.environ.get("IMAP_HOST", "imap.mail.yahoo.com")
IMAP_PORT   = int(os.environ.get("IMAP_PORT", "993"))
SMTP_HOST   = os.environ.get("SMTP_HOST", "smtp.mail.yahoo.com")
SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))


def decode_str(s):
    if isinstance(s, bytes):
        return s.decode("utf-8", errors="replace")
    if isinstance(s, str):
        parts = decode_header(s)
        result = []
        for part, enc in parts:
            if isinstance(part, bytes):
                result.append(part.decode(enc or "utf-8", errors="replace"))
            else:
                result.append(part)
        return "".join(result)
    return str(s or "")


def imap_connect():
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(EMAIL_ADDR, EMAIL_PASS)
    return imap


def fetch_envelopes(imap, uid_list, max_body=200):
    """Fetch subject/from/date for a list of UIDs."""
    results = []
    uid_str = ",".join(str(u) for u in uid_list)
    _, data = imap.uid("FETCH", uid_str, "(ENVELOPE BODY[TEXT]<0.500>)")
    # Parse in pairs (some servers send extra lines)
    i = 0
    while i < len(data):
        raw = data[i]
        if isinstance(raw, tuple):
            msg_data = b""
            # Collect all parts for this message
            for part in data[i:]:
                if isinstance(part, tuple):
                    msg_data += part[1] if len(part) > 1 else b""
                else:
                    break
            msg = email.message_from_bytes(b"Content-Type: text/plain\r\n\r\n" + msg_data)
            # Try to parse envelope from raw bytes
            raw_str = raw[0].decode("utf-8", errors="replace") if isinstance(raw[0], bytes) else str(raw[0])
            # Extract UID from the response
            uid_match = None
            import re
            uid_m = re.search(r'UID (\d+)', raw_str)
            uid_val = uid_m.group(1) if uid_m else "?"
            # Extract subject from raw (envelope)
            subj_m = re.search(r'ENVELOPE \(.*?"(.*?)"', raw_str)
            subj = decode_str(subj_m.group(1)) if subj_m else "(no subject)"
            results.append({"uid": uid_val, "subject": subj})
        i += 1
    return results


def cmd_list(args):
    imap = imap_connect()
    folder = args.folder or "INBOX"
    imap.select(f'"{folder}"', readonly=True)
    _, data = imap.uid("SEARCH", None, "ALL")
    uids = data[0].split()
    uids = uids[-args.limit:]  # most recent N

    if not uids:
        print(f"📭 {folder} 没有邮件")
        imap.logout()
        return

    uid_str = b",".join(uids)
    _, msgs = imap.uid("FETCH", uid_str, "(RFC822.HEADER)")

    print(f"📬 {folder} 最新 {len(uids)} 封邮件:\n")
    for i, item in enumerate(msgs):
        if not isinstance(item, tuple):
            continue
        raw_uid = uids[i] if i < len(uids) else b"?"
        msg = email.message_from_bytes(item[1])
        subj = decode_str(msg.get("Subject", "(no subject)"))
        sender = decode_str(msg.get("From", ""))
        date = msg.get("Date", "")
        print(f"  [{raw_uid.decode()}] {subj}")
        print(f"         From: {sender}")
        print(f"         Date: {date}\n")

    imap.logout()


def cmd_search(args):
    imap = imap_connect()
    imap.select('"INBOX"', readonly=True)
    query = args.query

    # Search in subject and body
    criteria = f'(OR SUBJECT "{query}" BODY "{query}")'
    _, data = imap.uid("SEARCH", None, criteria)
    uids = data[0].split()

    if not uids:
        print(f"🔍 没有找到包含 '{query}' 的邮件")
        imap.logout()
        return

    print(f"🔍 找到 {len(uids)} 封匹配邮件:\n")
    uid_str = b",".join(uids[-20:])  # limit to 20
    _, msgs = imap.uid("FETCH", uid_str, "(RFC822.HEADER)")

    uid_list = uids[-20:]
    for i, item in enumerate(msgs):
        if not isinstance(item, tuple):
            continue
        raw_uid = uid_list[i] if i < len(uid_list) else b"?"
        msg = email.message_from_bytes(item[1])
        subj = decode_str(msg.get("Subject", "(no subject)"))
        sender = decode_str(msg.get("From", ""))
        print(f"  [{raw_uid.decode()}] {subj}  |  {sender}")

    imap.logout()


def cmd_send(args):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDR
    msg["To"] = args.to
    msg["Subject"] = args.subject
    msg.attach(MIMEText(args.body, "plain", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(EMAIL_ADDR, EMAIL_PASS)
        smtp.sendmail(EMAIL_ADDR, [args.to], msg.as_string())

    print(f"✅ 已发送邮件给 {args.to}，主题：{args.subject}")


def cmd_move(args):
    """Search for emails matching a keyword and move them to a target folder."""
    imap = imap_connect()
    imap.select('"INBOX"')

    query = args.search
    folder = args.folder

    # Search
    _, data = imap.uid("SEARCH", None, f'(OR SUBJECT "{query}" BODY "{query}")')
    uids = data[0].split()

    if not uids:
        print(f"📭 没有找到包含 '{query}' 的邮件，无需移动")
        imap.logout()
        return

    print(f"找到 {len(uids)} 封匹配邮件，移动到 {folder}...")

    # Ensure target folder exists
    result, _ = imap.create(f'"{folder}"')
    # Ignore error if folder already exists

    # COPY all UIDs at once, then delete from INBOX
    uid_str = b",".join(uids)
    copy_status, copy_result = imap.uid("COPY", uid_str, f'"{folder}"')

    if copy_status == "OK":
        # Mark as deleted in INBOX
        imap.uid("STORE", uid_str, "+FLAGS", "\\Deleted")
        imap.expunge()
        print(f"✅ 已将 {len(uids)} 封邮件移动到 {folder}")
    else:
        print(f"❌ 移动失败: {copy_result}")

    imap.logout()


def cmd_flag(args):
    imap = imap_connect()
    imap.select('"INBOX"')

    flag_map = {"seen": "\\Seen", "unseen": "-\\Seen"}
    imap_flag = flag_map.get(args.flag.lower())
    if not imap_flag:
        print(f"❌ 未知 flag: {args.flag}，支持: seen, unseen")
        imap.logout()
        return

    action = "+FLAGS" if not imap_flag.startswith("-") else "-FLAGS"
    clean_flag = imap_flag.lstrip("-")
    imap.uid("STORE", str(args.uid).encode(), action, clean_flag)
    print(f"✅ UID {args.uid} 已标记为 {args.flag}")
    imap.logout()


def _safe_filename_part(value, fallback="unknown"):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._-")
    return value or fallback


def _extract_text_body(msg):
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    text = payload.decode(charset, errors="replace")
                except LookupError:
                    text = payload.decode("utf-8", errors="replace")
                chunks.append(text)
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            chunks.append(payload.decode(charset, errors="replace"))
        except LookupError:
            chunks.append(payload.decode("utf-8", errors="replace"))

    text = "\n".join(chunks)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_date_yyyy_mm_dd(msg):
    raw_date = msg.get("Date", "")
    if not raw_date:
        return "Unknown"
    try:
        dt = parsedate_to_datetime(raw_date)
        return dt.date().isoformat()
    except Exception:
        return "Unknown"


def _extract_vendor(msg):
    raw_from = decode_str(msg.get("From", ""))
    name, addr = parseaddr(raw_from)
    vendor = name.strip() or (addr.split("@")[0] if addr else "")
    return vendor or "Unknown"


def _extract_amount_currency(text):
    if not text:
        return ("Unknown", "Unknown")

    sym_map = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
    # e.g. USD 12.34 / 12.34 USD / $12.34
    patterns = [
        r"\b(USD|EUR|GBP|JPY|CNY|RMB|HKD|SGD|AUD|CAD)\s*([0-9]+(?:[.,][0-9]{2})?)\b",
        r"\b([0-9]+(?:[.,][0-9]{2})?)\s*(USD|EUR|GBP|JPY|CNY|RMB|HKD|SGD|AUD|CAD)\b",
        r"([$€£¥])\s*([0-9]+(?:[.,][0-9]{2})?)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if not m:
            continue
        g1, g2 = m.group(1), m.group(2)
        if g1 in sym_map:
            return (g2.replace(",", ""), sym_map[g1])
        if re.match(r"^[A-Za-z]{3}$", g1):
            return (g2.replace(",", ""), g1.upper())
        return (g1.replace(",", ""), g2.upper())
    return ("Unknown", "Unknown")


def _extract_invoice_number(text, subject):
    hay = f"{subject} {text}".strip()
    patterns = [
        r"\b(?:invoice|receipt|order|transaction|payment)\s*(?:number|no|#)?\s*[:#-]?\s*([A-Za-z0-9-]{4,})\b",
        r"\b(?:inv|txn|ord)[-_ ]?([A-Za-z0-9-]{4,})\b",
    ]
    for p in patterns:
        m = re.search(p, hay, re.IGNORECASE)
        if m:
            return m.group(1)
    return "Unknown"


def _classify_category(subject, text):
    hay = f"{subject} {text}".lower()
    rules = [
        ("travel", ["airline", "hotel", "booking", "trip", "flight", "train", "uber", "lyft"]),
        ("software", ["software", "license", "github", "openai", "aws", "gcp", "azure", "domain"]),
        ("shopping", ["order", "shipped", "store", "shop", "amazon", "taobao", "ebay"]),
        ("subscription", ["subscription", "renewal", "monthly", "yearly", "plan"]),
        ("utilities", ["electricity", "water", "gas", "internet", "phone bill", "utility"]),
    ]
    for cat, words in rules:
        if any(w in hay for w in words):
            return cat
    return "other"


def _is_receipt_mail(subject, text):
    hay = f"{subject} {text}".lower()
    keys = ["invoice", "receipt", "payment confirmation", "payment received", "paid"]
    return any(k in hay for k in keys)


def _raw_excerpt(text):
    if not text:
        return "Unknown"
    parts = re.split(r"(?<=[.!?])\s+", text)
    key_parts = []
    for p in parts:
        low = p.lower()
        if any(k in low for k in ["invoice", "receipt", "payment", "paid", "amount", "total"]):
            key_parts.append(p.strip())
        if len(key_parts) >= 3:
            break
    if not key_parts:
        key_parts = parts[:2]
    excerpt = " ".join(key_parts).strip()
    return excerpt[:800] if excerpt else "Unknown"


def cmd_scan_receipts(args):
    imap = imap_connect()
    folder = args.folder or "INBOX"
    imap.select(f'"{folder}"', readonly=True)
    _, data = imap.uid("SEARCH", None, "ALL")
    uids = data[0].split()
    uids = uids[-args.limit:]

    receipts_dir = Path(args.output_dir)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    if not uids:
        print("Status: Ignored")
        imap.logout()
        return

    for uid in uids:
        status, msg_data = imap.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            print("Status: Ignored")
            continue

        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_str(msg.get("Subject", ""))
        text = _extract_text_body(msg)

        if not _is_receipt_mail(subject, text):
            print("Status: Ignored")
            continue

        date_val = _extract_date_yyyy_mm_dd(msg)
        vendor = _extract_vendor(msg)
        amount, currency = _extract_amount_currency(text)
        invoice_no = _extract_invoice_number(text, subject)
        category = _classify_category(subject, text)

        safe_vendor = _safe_filename_part(vendor, "unknown_vendor")
        safe_inv = _safe_filename_part(invoice_no, "unknown_invoice")
        safe_date = date_val if re.match(r"^\d{4}-\d{2}-\d{2}$", date_val) else "unknown-date"
        out_file = receipts_dir / f"{safe_date}_{safe_vendor}_{safe_inv}.md"

        if out_file.exists():
            print("Status: Duplicate")
            continue

        summary = f"{vendor} 的一笔 {amount} {currency} 消费记录。"
        excerpt = _raw_excerpt(text)

        vendor_out = vendor if vendor else "Unknown"
        amount_out = amount if amount else "Unknown"
        currency_out = currency if currency else "Unknown"
        invoice_out = invoice_no if invoice_no else "Unknown"
        date_out = date_val if date_val else "Unknown"
        category_out = category if category else "Unknown"

        content = (
            f"# Receipt - {vendor_out}\n\n"
            f"- Date: {date_out}\n"
            f"- Vendor: {vendor_out}\n"
            f"- Amount: {amount_out} {currency_out}\n"
            f"- Invoice Number: {invoice_out}\n"
            f"- Category: {category_out}\n\n"
            f"---\n\n"
            f"## Summary\n\n"
            f"{summary}\n\n"
            f"---\n\n"
            f"## Raw Email Excerpt\n\n"
            f"{excerpt}\n"
        )
        out_file.write_text(content, encoding="utf-8")
        print("Status: Saved")

    imap.logout()


def main():
    if not EMAIL_ADDR or not EMAIL_PASS:
        print("❌ 缺少邮箱凭据，请设置环境变量：EMAIL_ADDRESS, EMAIL_APP_PASSWORD")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Yahoo Mail CLI")
    sub = parser.add_subparsers(dest="cmd")

    # list
    p_list = sub.add_parser("list", help="列出邮件")
    p_list.add_argument("--limit", type=int, default=10)
    p_list.add_argument("--folder", default="INBOX")

    # search
    p_search = sub.add_parser("search", help="搜索邮件")
    p_search.add_argument("query")

    # send
    p_send = sub.add_parser("send", help="发送邮件")
    p_send.add_argument("--to", required=True)
    p_send.add_argument("--subject", required=True)
    p_send.add_argument("--body", required=True)

    # move
    p_move = sub.add_parser("move", help="批量移动邮件")
    p_move.add_argument("--search", required=True, help="搜索关键词")
    p_move.add_argument("--folder", required=True, help="目标文件夹")

    # flag
    p_flag = sub.add_parser("flag", help="标记邮件")
    p_flag.add_argument("--uid", required=True, type=int)
    p_flag.add_argument("--flag", required=True, choices=["seen", "unseen"])

    # scan-receipts
    p_scan = sub.add_parser("scan-receipts", help="扫描并提取收据邮件")
    p_scan.add_argument("--limit", type=int, default=20, help="扫描最近 N 封邮件")
    p_scan.add_argument("--folder", default="INBOX", help="扫描文件夹")
    p_scan.add_argument("--output-dir", default="./receipts", help="输出目录")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "list": cmd_list,
        "search": cmd_search,
        "send": cmd_send,
        "move": cmd_move,
        "flag": cmd_flag,
        "scan-receipts": cmd_scan_receipts,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
