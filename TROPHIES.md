# 🏆 Trophies

Real-world vulnerabilities discovered with codebadger.

If you found a vulnerability using codebadger, open a pull request adding it here — include the CVE ID, project, a one-line description, and the date.

---

## 2026

### CVE-2026-1801 — libsoup · HTTP Request Smuggling · February 11, 2026

libsoup contains an HTTP request smuggling vulnerability in its chunked transfer encoding parser. The library accepts lone LF (`\n`) characters instead of requiring CRLF (`\r\n`) as mandated by RFC 9112, allowing an attacker to desynchronize a shared connection between a proxy and a backend server. **Rewarded with a bug bounty.**

**Project:** [GNOME/libsoup](https://gitlab.gnome.org/GNOME/libsoup)
**Severity:** High

---

### CVE-2025-51602 — VLC Media Player · Out-of-Bounds Read · February 11, 2026

Out-of-bounds read vulnerability in the MMS component of VLC Media Player, discovered as part of ongoing vulnerability analysis at QCRI. A crafted MMS stream can trigger the read, potentially leading to information disclosure or a crash.

**Project:** [VideoLAN/vlc](https://code.videolan.org/videolan/vlc)
**Severity:** Medium

---

### CVE-2025-6170 — libxml2 · Stack Buffer Overflow in xmllint · February 2, 2026

Vulnerability in the interactive shell of the `xmllint` command-line tool. When a user inputs an overly long command, the program writes beyond the bounds of a fixed-size stack buffer, causing memory corruption.

**Project:** [GNOME/libxml2](https://gitlab.gnome.org/GNOME/libxml2)
**Severity:** Medium

---

### CVE-2025-6021 — libxml2 · Integer Overflow → Stack Buffer Overflow · February 2, 2026

Integer overflows in buffer size calculations inside `xmlBuildQName` can lead to a stack-based buffer overflow, resulting in memory corruption and potential code execution.

**Project:** [GNOME/libxml2](https://gitlab.gnome.org/GNOME/libxml2)
**Severity:** High

---

### CVE-2025-6491 — php-src · Integer Overflow in SoapVar · February 2, 2026

If a `SoapVar` instance is created with a fully qualified name larger than 2 GB, integer overflows in size calculations lead to a heap buffer overflow, causing memory corruption.

**Project:** [php/php-src](https://github.com/php/php-src)
**Severity:** High

---

*Open a pull request to add your finding.*
