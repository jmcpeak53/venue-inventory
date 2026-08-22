# Slice 3 — Anonymous bookings and access-code login

**Status:** Ready for implementation  
**Blocked by:** Slice 2

## Outcome

Let the administrator create an anonymous booking using only an event date and
hand off a strong code that gives the booking party access to an empty customer
portal. The plaintext code is visible exactly once and never recoverable from
application storage.

## Scope

- Add booking schema with immutable non-secret reference, unique keyed code
  digest, event date, revision, and UTC timestamps.
- Generate 12 case-insensitive characters excluding ambiguous characters and
  display them as `XXXX-XXXX-XXXX`.
- Show the code only on the creation-success response with a copy control and
  explicit recovery warning.
- Normalize code entry and use HMAC-SHA-256 with an environment-only secret for
  indexed lookup; never put codes in URLs or normal logs.
- Add rate-limited customer code entry, generic failure behavior, opaque
  server-side 30-day sessions, an empty authenticated portal, and logout.
- Add a minimal admin booking list showing reference, event date, and created
  time, plus a booking detail shell.
- Allow any past, present, or future event date and keep access valid until the
  booking is deleted.

## Acceptance criteria

- [ ] Admin can create a booking with only an event date and receives one
      human-readable access code once.
- [ ] The database and logs contain a digest but never the plaintext code.
- [ ] Booking references are unique, immutable, visible to admin, and cannot be
      used for customer login.
- [ ] Normalized code entry authenticates the correct booking; bad and
      throttled attempts are indistinguishable.
- [ ] Customer sessions persist for 30 days, use secure opaque cookies, and end
      on logout.
- [ ] Past event dates do not prevent login.
- [ ] Deleting a booking transactionally removes its sessions and makes its
      code unusable.
- [ ] There is no code display, recovery, reset, or regeneration action after
      creation.

## Testing approach

- Test generation alphabet, formatting, normalization, uniqueness retry, and
  digest-only persistence through public behavior and database inspection at
  the security boundary.
- Test rate limiting, session expiry, logout, deletion invalidation, and CSRF.
- Capture application/proxy-style logs in tests and assert submitted codes are
  absent.
- Use an injected Chicago date for past/present/future cases without making it
  an access rule.

## Out of scope

Basket selections, code regeneration, names/contact data, status, email
sending, booking filters, and printable preparation lists remain out of scope.

## Suggested implementation models

- **Anthropic:** Claude Sonnet 5 at high effort
- **OpenAI:** GPT-5.6 Terra at high reasoning effort

## Top runtime failure points

1. The HMAC secret changes between deployments. Every existing code begins
   returning the same generic login failure even though bookings still exist.
2. Proxy-IP trust is wrong. Rate limiting groups all customers behind Caddy or
   trusts spoofed headers, appearing either as mass lockout or ineffective
   throttling.
3. Session cleanup does not cascade on booking deletion. A deleted booking's
   old browser appears logged in until its next database lookup; requests must
   instead invalidate immediately and return to code entry.

