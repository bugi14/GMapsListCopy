# Google Maps saved-list access strategy

_Research completed: 2026-10-06_

## Decision

Build the first version around a Google Takeout export selected by the user. Parse the `Saved` collections CSV files locally, preserve each item's Google URL as an opaque reference, and keep all intermediate data on the user's machine.

Do not make the first version depend on Google OAuth or the Data Portability API. That API is a possible later source adapter, but it requires a Google Cloud project, billing, application verification, a privacy policy, and compliance with the Data Portability API policy. Its stated approved use is moving data from Google to another platform or service, so using it only to copy data back into Google Maps needs confirmation from Google before release.

There is no documented public API for creating a Google Maps saved list or adding places to one. Destination writes therefore need a separate, explicitly experimental decision. The safest initial product boundary is an assisted workflow that consumes the exported item URLs and keeps the user in control of actions in Google Maps. Fully automated browser interaction should not be presented as supported by Google and should not be enabled by default without a terms and reliability review.

## Findings

### Supported read paths

Google Maps Help explicitly directs users to Google Takeout and the `Saved` product to export saved lists. This is the lowest-friction source for an MVP because the export is initiated by the account owner and does not require the project to handle Google credentials or tokens.

The Data Portability API exposes the sensitive OAuth scope:

`https://www.googleapis.com/auth/dataportability.saved.collections`

The `saved.collections` resource group exports collections in CSV format. Documented fields include:

- `collection_description`
- `title`
- `note`
- `item_content_url`
- `tags`
- `comment`

The export covers items saved across Google Search, Maps, and other Google services. Code must therefore filter for Google Maps item URLs instead of assuming every row represents a Maps place.

Google also exposes a restricted `dataportability.maps.starred_places` scope, but that scope covers only the built-in Starred places list and is not a substitute for `saved.collections` when custom or shared lists are required.

### Identifier quality

The documented saved-collection schema does not expose a dedicated Google Maps Place ID. It exposes `item_content_url`; Google's example URL contains an internal Maps identifier, but the schema does not promise that this embedded value is stable or present in every row. The implementation should retain the full URL unchanged and treat any extracted identifier as a best-effort optimization only.

### No supported write path

The Data Portability API is export-only. Its documented REST surface starts archive jobs, checks or retries their state, checks access type, and resets authorization. It has no method to create a Maps list or save a place.

The Places API provides place search and place-detail operations, not access to a user's saved lists and not list mutation. Google Maps Help documents creating and updating lists only through the Google Maps user interface.

### OAuth and release constraints

If a later version adds the Data Portability adapter, it should:

- request only `dataportability.saved.collections` when the user starts an import;
- use Authorization Code with PKCE for a local desktop application;
- store tokens only in the operating system credential store;
- never log, commit, or store access and refresh tokens in plaintext;
- handle one-time and time-based consent separately and revoke access when it is no longer needed;
- account for regional and age eligibility;
- complete Google's application verification and publish a privacy policy before public release.

One-time access permits one export per scope. Time-based consent permits exports no more often than once every 24 hours for the consent period. Archive creation can take minutes to hours, and Google's quickstart states that jobs can take up to seven days.

### Terms and safety boundary

Google's Maps terms prohibit bulk downloading Maps content and creating substitute mapping datasets. Google's general terms also restrict automated access that violates machine-readable instructions. A list-copy tool should avoid scraping place pages or building its own place database. It should operate on data the user explicitly exported, preserve Google URLs as references, minimize requests, and disclose that any browser-driven destination workflow is unofficial and may break when Google Maps changes.

## Recommended delivery sequence

1. Implement a local Takeout importer and list selector.
2. Normalize exported rows while retaining the original CSV values and complete item URLs.
3. Prototype a user-controlled destination workflow against a small list and document every manual and automated step.
4. Before enabling unattended writes, validate the approach against current Google terms and test account-safety behavior.
5. Consider a Data Portability source adapter only after confirming that this same-service copying use case is eligible for approval.

## Sources

- [Google Maps Help: Create a list of places](https://support.google.com/maps/answer/7280933)
- [Data Portability API: Saved schema reference](https://developers.google.com/data-portability/schema-reference/save)
- [Data Portability API: Available OAuth scopes](https://developers.google.com/data-portability/user-guide/scopes)
- [Data Portability API overview](https://developers.google.com/data-portability/user-guide/overview)
- [Data Portability API REST reference](https://developers.google.com/data-portability/reference/rest)
- [Data Portability API user data and developer policy](https://developers.google.com/data-portability/policy)
- [Google OAuth 2.0 best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
- [Places API REST reference](https://developers.google.com/maps/documentation/places/web-service/reference/rest)
- [Google Maps End User Additional Terms](https://maps.google.com/help/terms_maps/)
- [Google Terms of Service](https://policies.google.com/terms)
