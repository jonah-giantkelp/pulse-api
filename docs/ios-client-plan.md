# iOS Client — API Integration Plan

## 1. Auth

The backend validates every request by calling `supabase.auth.get_user(token)` — it extracts the user ID from a Supabase-issued JWT. The iOS client needs to obtain and manage that JWT.

### How it works end-to-end

1. **Sign up / sign in** — hit Supabase's auth REST endpoint directly:
   ```
   POST {SUPABASE_URL}/auth/v1/token?grant_type=password
   Headers:
     apikey: {SUPABASE_ANON_KEY}
     Content-Type: application/json
   Body:
     { "email": "...", "password": "..." }
   ```
   Response contains `access_token` (JWT, ~1hr expiry) and `refresh_token`.

2. **Every API call** — attach the access token:
   ```
   GET /me/events
   Headers:
     Authorization: Bearer {access_token}
   ```
   The Flask backend's `@require_auth` decorator calls `supabase.auth.get_user(token)` to validate the JWT and extract `user_id`. If invalid/expired → 401.

3. **Token refresh** — before the access token expires, use the refresh token:
   ```
   POST {SUPABASE_URL}/auth/v1/token?grant_type=refresh_token
   Headers:
     apikey: {SUPABASE_ANON_KEY}
     Content-Type: application/json
   Body:
     { "refresh_token": "..." }
   ```
   Returns a new `access_token` + `refresh_token` pair.

### Using supabase-swift vs. raw HTTP

You can use the **Supabase Swift SDK** (`supabase-swift`) which wraps all of the above:

```swift
let supabase = SupabaseClient(
    supabaseURL: URL(string: "https://pyqgugkmccyotnkgdffv.supabase.co")!,
    supabaseKey: "<ANON_KEY>"  // the public anon key, NOT the service_role key
)

// Sign up
try await supabase.auth.signUp(email: email, password: password)

// Sign in
let session = try await supabase.auth.signIn(email: email, password: password)
// session.accessToken is the JWT to send to the Flask API

// Get current token (SDK auto-refreshes before expiry)
let token = try await supabase.auth.session.accessToken
```

The SDK persists the session in Keychain automatically and handles refresh. But the SDK is **only used for auth** — all data reads/writes go through the Flask API, not Supabase client queries.

### Important: anon key vs. service_role key

The iOS app must use the **anon (public) key** — this is the one safe to embed in the client. The backend uses the **service_role key** (secret, server-only) to write to tables that bypass RLS. Never ship the service_role key in the app.

### Auth flow in the app

1. App launch → check `supabase.auth.session` for existing session
2. If nil or expired and refresh fails → show login/signup screen
3. On sign-in success → store session (SDK does this), navigate to main app
4. On 401 from any API call → attempt `supabase.auth.refreshSession()`, retry once → if still 401, force re-login
5. Sign out → `supabase.auth.signOut()`, clear local caches, return to login

---

## 2. API Client

Build a single `PulseAPIClient` that wraps all backend calls. It talks to the Flask API, **not** directly to Supabase tables (the API handles writes with service_role key and business logic).

### Base config

```
baseURL:  <your deployed Flask API URL>
headers:  { "Authorization": "Bearer \(session.accessToken)" }
```

Use `URLSession` or a lightweight wrapper (e.g. plain async/await with `URLRequest`). No need for Alamofire.

### Endpoints to implement

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/artists/search?q=&limit=` | MusicBrainz search (artist add flow) |
| `POST` | `/artists` | Add artist + auto-track |
| `GET` | `/artists/{id}` | Artist detail (platform IDs, image, genres) |
| `GET` | `/artists/{id}/events` | Events for one artist |
| `GET` | `/artists/{id}/social` | Recent social posts |
| `GET` | `/artists/{id}/social/summary` | AI social summary |
| `GET` | `/me/artists` | All tracked artists |
| `DELETE` | `/me/artists/{id}` | Untrack artist |
| `GET` | `/me/events` | Aggregated upcoming events (all tracked artists). Supports `?city=` and `?country=` filters. |
| `GET` | `/me/email-preferences` | Get digest + location preferences |
| `PUT` | `/me/email-preferences` | Update digest + default locations |

### Response models (Codable structs)

```swift
struct ArtistSearchResult: Codable {
    let musicbrainzId: String
    let name: String
    let disambiguation: String?
    let country: String?
    let tags: [String]?
    let imageUrl: String?
}

struct Artist: Codable {
    let id: UUID
    let name: String
    let musicbrainzId: String?
    let spotifyId: String?
    let genres: [String]?
    let imageUrl: String?
    let active: Bool
}

struct Event: Codable {
    let id: UUID
    let title: String
    let date: Date
    let venue: String?
    let city: String?
    let source: String
    let ticketUrl: String?
    let artistBilling: String?
    let artists: [EventArtist]?  // present in /me/events
    let images: [EventImage]?    // posters/flyers from ticketing + social
}

struct EventImage: Codable {
    let imageUrl: String
    let imageType: String        // "poster", etc.
}

struct EventArtist: Codable {
    let artistId: UUID
    let name: String
    let imageUrl: String?
    let billing: String?
}

struct SocialPost: Codable {
    let id: UUID
    let platform: String
    let caption: String?
    let mediaUrl: String?
    let postedAt: Date
}

struct SocialSummary: Codable {
    let summary: String
    let date: String
    let createdAt: Date
}

struct UserArtist: Codable {
    let artistId: UUID
    let city: String?
    let notify: Bool
    let createdAt: Date
    let artist: Artist  // nested from Supabase join
}

struct EmailPreferences: Codable {
    let email: String?
    let digestEnabled: Bool
    let defaultCities: [String]
    let defaultCountries: [String]
}
```

Use `JSONDecoder` with `.convertFromSnakeCase` key strategy globally.

---

## 3. Add Artist Flow

This is the most important interaction. Two stages: **search** then **commit**.

### Stage 1 — Search (MusicBrainz via API)

1. User types in a search field
2. Debounce input (~400ms) to avoid spamming
3. Call `GET /artists/search?q=<query>&limit=8`
4. Display results as a list:
   - **name** (primary label)
   - **disambiguation** (secondary label, e.g. "UK electronic duo")
   - **country** (flag or text)
   - **tags** (genre chips, if present)
   - **image_url** (thumbnail, if present)
5. Disambiguation is critical — many artists share names. Show it prominently.

### Stage 2 — Commit (Add/Track)

1. User taps a result
2. Call `POST /artists` with body:
   ```json
   {
     "musicbrainz_id": "<selected.musicbrainzId>",
     "name": "<selected.name>",
     "city": "Berlin"
   }
   ```
3. API returns `{ artist_id, status, message }` where status is:
   - `"new"` — artist was created + platforms resolved via AI + user subscribed
   - `"existing"` — artist already in DB, user just got subscribed
4. In **both cases** the user is now tracking the artist. Navigate to artist detail or back to list.
5. Show the `message` string briefly (toast/banner) — it tells the user what happened.

### Edge cases

- **Already tracking**: API returns 409 if user already tracks this artist. Show friendly message ("You're already tracking X").
- **Resolution in progress**: The `POST /artists` call can be slow (~5-10s) for new artists because it resolves platform IDs via AI. Show a loading state that explains this ("Setting up artist..."). Consider making this feel intentional rather than broken.
- **No results**: Show empty state with suggestion to try alternate spellings or full name.

---

## 4. Data Strategy

### What to read from the API vs. Supabase directly

| Data | Source | Why |
|------|--------|-----|
| Artist search | Flask API (`/artists/search`) | MusicBrainz proxy with formatting |
| Add/track artist | Flask API (`POST /artists`) | Triggers AI resolution server-side |
| Tracked artists list | Flask API (`/me/artists`) | Joins artist data, respects RLS |
| Events | Flask API (`/me/events`) | Dedup + multi-artist grouping logic |
| Social posts/summaries | Flask API | Formatted, paginated |
| Untrack | Flask API (`DELETE /me/artists/{id}`) | Server validates ownership |

**Do not** read/write Supabase tables directly from the iOS client. The Flask API is the contract — it handles service_role writes, AI orchestration, and business logic that the client shouldn't replicate.

The Supabase Swift SDK is only used for **auth** (sign-up, sign-in, session management).

### Caching

- Cache `/me/artists` and `/me/events` locally (simple disk cache or SwiftData).
- Show cached data immediately on launch, then refresh in background.
- Artist search results should **not** be cached (always live).
- Social posts/summaries: cache per-artist with short TTL (~30 min).

### Realtime (optional, future)

Supabase Realtime could subscribe to `events` or `social_posts` inserts for tracked artists, but this is not needed for v1. The daily sync cadence means polling on app-foreground is sufficient.

---

## 5. Network Layer Shape

```
PulseAPIClient
├── init(supabaseClient: SupabaseClient)
├── searchArtists(query: String, limit: Int) async throws -> [ArtistSearchResult]
├── addArtist(musicbrainzId: String, name: String, city: String) async throws -> AddArtistResponse
├── getArtist(id: UUID) async throws -> Artist
├── getTrackedArtists() async throws -> [UserArtist]
├── untrackArtist(id: UUID) async throws
├── getMyEvents(city: String?, country: String?) async throws -> [Event]
├── getEmailPreferences() async throws -> EmailPreferences
├── updateEmailPreferences(email: String?, digestEnabled: Bool?, defaultCities: [String]?, defaultCountries: [String]?) async throws -> EmailPreferences
├── getArtistEvents(id: UUID) async throws -> [Event]
├── getArtistSocial(id: UUID) async throws -> [SocialPost]
├── getArtistSocialSummary(id: UUID) async throws -> SocialSummary?
└── (private) authorizedRequest(method:path:body:) async throws -> Data
```

`authorizedRequest` pulls the current JWT from `supabaseClient.auth.session`, builds the `URLRequest`, and throws a typed error on 401/403/404/500.

---

## 6. Dependencies

| Package | Purpose |
|---------|---------|
| `supabase-swift` (SPM) | Auth only — sign-up, sign-in, JWT session/token management |

That's it. No other SDKs needed. The Flask API is the single interface for all data operations.

The Supabase client is initialised once with the **anon key** (public, safe to bundle). The service_role key stays server-side only.

---

## 7. Error Handling

| HTTP Status | Meaning | iOS Action |
|-------------|---------|------------|
| 401 | Token expired/invalid | Attempt refresh → if fails, force re-login |
| 403 | Not authorized for resource | Show error |
| 404 | Artist/resource not found | Show not-found state |
| 409 | Already tracking artist | Show "already tracking" message |
| 422 | Validation error (e.g. bad MBID) | Show API error message |
| 500 | Server error | Retry once, then show generic error |

The API returns `{ "error": "message" }` on all error responses — parse and display that.
