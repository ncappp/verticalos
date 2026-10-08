# FaxClip — Telegram Mini App + physical Android bridge

## v14
Upload MP4 and description in **Publications → Upload and publish** in Telegram. Explicit public-post and rights consent are required. The queue dispatches to a Mac USB bridge; APK v14 imports/hash-checks media, sets the exact description, submits once, checks the first public profile tile and reopens its fresh post URL twice. UI reports application-level confirmation, not platform API/public-viewer or binary-transcoded identity proof.

**Publications → Connect Mac** generates a one-use 10-minute pairing code. Redeem it locally in the cloud bridge launcher, not in chat. Device bearer tokens are scoped, stored locally with owner-only permissions, never displayed in the UI. A running job blocks re-pairing. Regenerating a code invalidates the old code; successful re-pairing invalidates old bridge credentials.

Supported route: Redmi 23053RN02Y, Android 15, TikTok 44.6.4, @redmaagi. Other platforms/accounts are disabled pending calibration. The Mac must stay on, bridge running, phone USB-authorized and manually unlocked. No account login, unknown security dialogs or unlocking are automated.

Server: existing Docker/gunicorn setup; configure Telegram token and explicit allowed-owner IDs locally, HTTPS and durable data storage. ALLOW_DEV_AUTH=0; FAXCLIP_LOCAL_TOKEN must be unset in production. Current render.yaml retains free plan and has no persistent disk: SQLite, uploads and pairing state can be lost on redeploy/restart. This deployment must not be sold as a durable media archive. Pricing/storage plan is not changed without owner approval.

UI/API job idempotency + immutable source-SHA guard, durable pre-submit intent, Android attempt journal and no automatic retries prevent blind reposting. Lease expiry/unknown results stop the device queue with NEEDS_REVIEW. Earlier matched caption URL is captured before publishing and cannot verify a new submission. Do not erase phone journals or private bridge configs to bypass the safeguard.

No uiautomator in the gated route because diagnostic XML collection interrupted service state. New job binding is required after service reconnection. Foreground clipboard access is used solely for fresh, strictly validated TikTok URLs; arbitrary clipboard text is not logged.

Test status at commit: 33 server/model tests, 127 Android model checks, and isolated browser form tests passed. Physical v13 publication+profile+URL flow was observed on owner phone. Full Telegram queue → v14 → new actual post remains to be tested after deployment.

Device software is delivered as a separate Mac bridge + signed APK kit; Python is an internal bridge component, not the video-upload UI. Owners use Telegram for uploads. Repo does not contain production tokens, runtime databases, videos or signing keys.

## Automatic verification update (free test mode)
Bounded post-link/foreground reinspection runs in the normal bridge cycle. An fsynced, per-job local checkpoint retains the verified import name, source hash, exact caption, prior post URL and captured post URL. Recovery uses a device-authenticated VERIFYING lease restricted to submitted jobs; it cannot download media, reset the phase or authorize publication. Completion still requires two UI reopen confirmations, matching source/caption, and PNG evidence. Unresolved results remain NEEDS_REVIEW. Old jobs without checkpoints are not reconstructed or resubmitted.
The Android helper checks clipboard write timestamps when text is unchanged, and reads only with foreground window focus. Replacement APK installation preserves native duplicate guards; do not uninstall or clear app data. This does not prove binary identity of TikTok-transcoded video.
Render remains Free, with ephemeral SQLite/uploads. Deployment/restart can lose queue, tokens and server guards; this release is a free test setup, not durable storage. No payment or persistent disk was enabled.

## Devices-first registration and multi-device manager
Device registration/pairing is now independent of platform, account and brand. Owner-issued ten-minute one-use setup files are processed by a running Mac manager from Downloads; no pairing-code typing is required. Multiple unbound USB phones require an explicit serial; ambiguity fails closed. Different phones have separate credentials, workers, device state and phone locks. The manager never silently installs an accessibility service or grants Android permissions.
Publications contains upload and information actions; pairing resides under Devices. Registration supports TikTok, YouTube, Instagram and VK Video. Adapter registry explicitly reports NOT_IMPLEMENTED for the latter three. Existing TikTok publication/verification calibration is still account-scoped: this update does not claim other accounts or Android application versions were tested.
A platform/account/source-hash guard now prevents retrying the same file from another phone as well. Existing job payloads populate that guard on startup; free Render data loss remains a limitation.
