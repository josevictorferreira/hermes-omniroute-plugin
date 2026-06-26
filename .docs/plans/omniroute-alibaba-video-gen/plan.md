# PR Plan: Alibaba (DashScope) Video Generation in OmniRoute

**Upstream repo:** `diegosouzapw/OmniRoute`
**Goal:** Route `POST /v1/videos/generations` with `model: "alibaba/wan2.7-t2v"` (and siblings) to Alibaba Cloud Model Studio's DashScope video-synthesis API.
**Scope:** 2 code edits, 1 new test file. No credential plumbing, no dashboard changes, no new provider onboarding.

---

## Context (why this is small)

- The `alibaba` provider **already exists** (`open-sse/config/providers/registry/alibaba/index.ts`): `id: "alibaba"`, `authType: "apikey"`, `authHeader: "bearer"`, Bearer DashScope key. Video reuses the same stored credential — no new auth path.
- Credential resolution for video already works: `resolveVideoCredentialProvider("alibaba")` returns `"alibaba"` unchanged (`googleFlow.ts:31-33` only remaps `googleflow`).
- Video is dispatched by a hardcoded `format` string in an `if`-chain (`open-sse/handlers/videoGeneration.ts:61-103`). Adding a format = one registry entry + one branch + one handler function.
- The Alibaba Wan API is **async (create task → poll)** — identical pattern to the existing `handleKieVideoGeneration` (`videoGeneration.ts:426-560`), which is the reference template.

---

## Alibaba Wan wire protocol (verified)

Source: [Wan2.7 text-to-video API reference](https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference), cross-checked against ComfyUI `nodes_wan.py` and ArcReel `dashscope.py`.

### Step 1 — Create task
```
POST https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
Authorization: Bearer $DASHSCOPE_API_KEY
Content-Type: application/json
X-DashScope-Async: enable

{
  "model": "wan2.7-t2v",
  "input": { "prompt": "...", "negative_prompt": "..." },
  "parameters": { "size": "1280*720", "duration": 5 }
}
```
Response:
```json
{ "output": { "task_id": "abc123", "task_status": "PENDING" }, "request_id": "..." }
```

### Step 2 — Poll until terminal
```
GET https://dashscope-intl.aliyuncs.com/api/v1/tasks/{task_id}
Authorization: Bearer $DASHSCOPE_API_KEY
```
Response (terminal):
```json
{
  "output": { "task_status": "SUCCEEDED", "video_url": "https://...mp4" },
  "request_id": "...",
  "usage": { "video_count": 1 }
}
```
Status transitions: `PENDING` → `RUNNING` → `SUCCEEDED` | `FAILED`. Tasks take 1–5 min.

### Footguns
1. **Region-locked hosts** (cross-region silently fails): `dashscope-intl.aliyuncs.com` (Singapore/intl) vs `dashscope.aliyuncs.com` (China). A single registry `baseUrl` holds one region.
2. **Protocol split:** wan2.7 uses the `/video-synthesis` "new protocol" endpoint. wan2.1/2.5/2.6 historically used an older `/generation` endpoint. If listing both, the handler must branch on model family.
3. **Size format:** Alibaba uses `WIDTHxHEIGHT` with `*` separator (`1280*720`), not the `WIDTHxHEIGHT` / `16:9` ratio string OmniRoute normalizes elsewhere.

---

## Decisions (resolved to keep the PR minimal)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Scope to wan2.7 only, or include 2.1/2.5/2.6? | **wan2.7 only** for v1 | wan2.7 is the current model, single endpoint, avoids the protocol-split branch. Older models can be a follow-up. |
| 2 | Region: intl or CN? | **intl (Singapore)** | The existing `alibaba` chat provider uses `dashscope-intl`. Matching it keeps one credential working for both chat + video. CN can be a separate `alibaba-cn` entry later (precedent: `registry/alibaba/cn/index.ts` exists for chat). |
| 3 | Negative prompt / size / duration passthrough? | **All optional, mapped** | `body.negative_prompt`, `body.size` (converted to `WxH` if given as ratio), `body.duration` (number→string). |
| 4 | Model the handler on kie or vertex? | **kie** | Both are async task-based; kie's polling loop (`timeout_ms` / `poll_interval_ms` overrides) is directly reusable. |

---

## Implementation

### Edit 1 — Registry entry
**File:** `open-sse/config/videoRegistry.ts`
**Location:** add a key to `VIDEO_PROVIDERS` (after the existing entries, before the closing `};` around line 195).

```ts
alibaba: {
  id: "alibaba",
  alias: "ali",
  baseUrl: "https://dashscope-intl.aliyuncs.com/api/v1",
  statusUrl: "https://dashscope-intl.aliyuncs.com/api/v1/tasks",
  authType: "apikey",
  authHeader: "bearer",
  format: "dashscope-video",
  models: [
    { id: "wan2.7-t2v", name: "Wan 2.7 T2V" },
  ],
},
```

**Effect of Edit 1 alone:** model appears in `GET /v1/videos/generations` catalog + dashboard `/media-providers/video` (auto-surfaced via `mediaServiceKinds.ts`). Requests still fail with `Unsupported video format: dashscope-video` until Edit 2.

### Edit 2 — Dispatch branch + handler
**File:** `open-sse/handlers/videoGeneration.ts`

**(a)** Add to the `if`-chain, before the fallthrough `return { success:false, ... Unsupported video format }` (line ~103):
```ts
if (providerConfig.format === "dashscope-video") {
  return handleDashscopeVideoGeneration({ model, provider, providerConfig, body, credentials, log });
}
```

**(b)** Implement `handleDashscopeVideoGeneration`, mirroring `handleKieVideoGeneration` (lines 426–560). Skeleton:

```ts
async function handleDashscopeVideoGeneration({
  model, provider, providerConfig, body, credentials, log,
}: {
  model: string;
  provider: string;
  providerConfig: { baseUrl: string; statusUrl?: string };
  body: Record<string, unknown> & {
    prompt?: unknown;
    negative_prompt?: unknown;
    size?: unknown;
    duration?: unknown;
    timeout_ms?: unknown;
    poll_interval_ms?: unknown;
  };
  credentials?: { apiKey?: string; accessToken?: string } | null;
  log?: { info: (s: string, m: string) => void; error: (s: string, m: string) => void } | null;
}) {
  const startTime = Date.now();
  const timeoutMs = Number(body.timeout_ms) > 0 ? Number(body.timeout_ms) : 300000;
  const pollIntervalMs = Number(body.poll_interval_ms) > 0 ? Number(body.poll_interval_ms) : 5000;
  const token = credentials?.apiKey || credentials?.accessToken;

  if (!token) {
    return { success: false, status: 401, error: "Alibaba DashScope API key is required" };
  }

  const baseUrl = providerConfig.baseUrl.replace(/\/$/, "");
  const statusUrl = (providerConfig.statusUrl || `${baseUrl}/tasks`).replace(/\/$/, "");
  const prompt = typeof body.prompt === "string" ? body.prompt : String(body.prompt ?? "");

  // Map OmniRoute size → Alibaba "WxH". Accept "16:9"/"1280*720"/"1280x720".
  const sizeParam = normalizeDashscopeSize(body.size, body.aspect_ratio);
  const durationParam = body.duration != null ? String(body.duration) : undefined;

  const payload: Record<string, unknown> = {
    model,
    input: {
      prompt,
      ...(typeof body.negative_prompt === "string" ? { negative_prompt: body.negative_prompt } : {}),
    },
    parameters: {
      ...(sizeParam ? { size: sizeParam } : {}),
      ...(durationParam ? { duration: Number(durationParam) } : {}),
    },
  };

  if (log) log.info("VIDEO", `${provider}/${model} (dashscope-video) | prompt: "${prompt.slice(0, 60)}..."`);

  try {
    // --- Step 1: create task ---
    const createRes = await fetch(`${baseUrl}/services/aigc/video-generation/video-synthesis`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
      },
      body: JSON.stringify(payload),
    });
    const createData = await createRes.json().catch(() => ({}));
    const taskId = createData?.output?.task_id;
    if (!taskId) {
      const errMsg = createData?.message || createData?.errors?.[0]?.message || "DashScope did not return task_id";
      if (log) log.error("VIDEO", `DashScope createTask failed: ${JSON.stringify(createData)}`);
      return { success: false, status: 502, error: errMsg };
    }

    // --- Step 2: poll ---
    const deadline = startTime + timeoutMs;
    let lastStatus = "PENDING";
    while (Date.now() < deadline) {
      await sleep(pollIntervalMs);
      const pollRes = await fetch(`${statusUrl}/${taskId}`, {
        headers: { "Authorization": `Bearer ${token}` },
      });
      const pollData = await pollRes.json().catch(() => ({}));
      lastStatus = pollData?.output?.task_status || "PENDING";

      if (lastStatus === "SUCCEEDED") {
        const videoUrl = pollData?.output?.video_url;
        if (!videoUrl) {
          return { success: false, status: 502, error: "DashScope SUCCEEDED but no video_url" };
        }
        saveCallLog({
          method: "POST", path: "/v1/videos/generations", status: 200,
          model: `${provider}/${model}`, provider, duration: Date.now() - startTime,
          responseBody: { videos_count: 1 },
        }).catch(() => {});
        return {
          success: true,
          data: { created: Math.floor(Date.now() / 1000), data: [{ url: videoUrl, format: "mp4" }] },
        };
      }
      if (lastStatus === "FAILED" || lastStatus === "UNKNOWN_ERROR") {
        const errMsg = pollData?.output?.message || pollData?.output?.errors?.[0]?.message || "DashScope task FAILED";
        return { success: false, status: 502, error: errMsg };
      }
      // else PENDING / RUNNING → keep polling
    }

    return { success: false, status: 504, error: `DashScope task ${taskId} timed out (status: ${lastStatus})` };
  } catch (err: any) {
    if (log) log.error("VIDEO", `DashScope video generation failed: ${err?.message}`);
    return {
      success: false,
      status: typeof err?.status === "number" ? err.status : 502,
      error: sanitizeErrorMessage(err),
    };
  }
}

// Ratio → Alibaba "WxH" (1280*720). Returns undefined if unparseable.
function normalizeDashscopeSize(size: unknown, aspectRatio: unknown): string | undefined {
  // Already Alibaba-shaped: "1280*720"
  if (typeof size === "string" && /^\d+\*\d+$/.test(size)) return size;
  // OpenAI-shaped: "1280x720"
  if (typeof size === "string" && /^\d+x\d+$/.test(size)) return size.replace("x", "*");
  // Ratio: "16:9" → closest supported resolution
  if (typeof aspectRatio === "string") {
    const map: Record<string, string> = { "16:9": "1280*720", "9:16": "720*1280", "1:1": "960*960" };
    return map[aspectRatio];
  }
  return undefined;
}

function sleep(ms: number) { return new Promise((r) => setTimeout(r, ms)); }
```

**Imports already present** in `videoGeneration.ts`: `saveCallLog` (line 35), `sanitizeErrorMessage` (line 36). No new imports needed.

> **Style note:** OmniRoute's other handlers use `requests`/`fetch` inconsistently — some use executors (kie), some call `fetch` inline (veoaifree). Inline `fetch` is acceptable here and matches the simpler handlers. Match whatever the surrounding code uses at PR time.

### Edit 3 — Test
**File (new):** `tests/unit/video-dashscope.test.ts`

Cover:
1. **create task** — stub `fetch` to return `{ output: { task_id: "t1" } }` once, then a SUCCEEDED poll → assert response shape `{ success, data:{ data:[{url, format:"mp4"}] } }`.
2. **no credentials** → `{ success:false, status:401 }`.
3. **create task fails** (no `task_id`) → `{ success:false, status:502 }`.
4. **task FAILED** → `{ success:false, status:502 }`.
5. **timeout** → `{ success:false, status:504 }`.
6. **size normalization** — `normalizeDashscopeSize("16:9", undefined)` === `"1280*720"`; passthrough `"1280*720"`.

Pattern: follow existing `tests/unit/video-*.test.ts` (verify path exists at PR time).

---

## Out of scope (explicitly deferred)

- **wan2.1/2.5/2.6 models** — different endpoint (`/generation`), follow-up PR.
- **China region (`alibaba-cn`)** — separate registry entry, follow-up.
- **Image-to-video / reference video (i2v/r2v)** — Wan supports `media[{type:"first_frame"}]`; out of scope for v1 text-to-video.
- **Audio dubbing input** (`audio_url`) — Wan 2.7 supports it; omit for v1.
- **Multi-shot / watermark params** — omit.

---

## Verification checklist (PR ready when all pass)

- [ ] `npm run typecheck` clean
- [ ] `npm test -- video-dashscope` green
- [ ] `GET /v1/videos/generations` lists `alibaba/wan2.7-t2v`
- [ ] `POST /v1/videos/generations` with `{model:"alibaba/wan2.7-t2v", prompt:"..."}` returns `{data:[{url, format:"mp4"}]}` against a real DashScope intl key
- [ ] Dashboard `/media-providers/video` shows the Alibaba provider
- [ ] No-credential request returns 401, not 500
- [ ] Polling respects `timeout_ms` / `poll_interval_ms` overrides

---

## Files touched

| File | Change |
|------|--------|
| `open-sse/config/videoRegistry.ts` | +1 entry in `VIDEO_PROVIDERS` |
| `open-sse/handlers/videoGeneration.ts` | +1 dispatch branch, +1 handler fn, +2 small helpers (`normalizeDashscopeSize`, `sleep`) |
| `tests/unit/video-dashscope.test.ts` | new test file |

**Estimated diff:** ~130 lines added, 0 deleted.
