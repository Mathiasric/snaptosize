const MAX_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB
const KV_TTL = 72 * 3600; // 72 hours

export default {
	async fetch(request: Request, env: any, ctx: ExecutionContext): Promise<Response> {
	  const url = new URL(request.url);
  
	  // POST /enqueue
	  if (url.pathname === "/enqueue" && request.method === "POST") {
		const cl = request.headers.get("Content-Length");
		if (cl) {
		  const n = parseInt(cl, 10);
		  if (!isNaN(n) && n > MAX_SIZE_BYTES) {
		    return new Response(JSON.stringify({ error: "Request too large (max 25MB)" }), {
		      status: 413,
		      headers: { "Content-Type": "application/json" },
		    });
		  }
		}
		let body: any = {};
		if (request.body) {
		  const capped = await readBodyWithLimit(request.body, MAX_SIZE_BYTES);
		  if (capped.overflow) {
		    return new Response(JSON.stringify({ error: "Request too large (max 25MB)" }), {
		      status: 413,
		      headers: { "Content-Type": "application/json" },
		    });
		  }
		  try {
		    body = JSON.parse(new TextDecoder().decode(capped.bytes));
		  } catch {
		    body = {};
		  }
		}
		const jobId = crypto.randomUUID();
  
		const job = {
		  job_id: jobId,
		  created_at: Date.now(),
		  payload: body,
		};
  
		await env.y.put(
		  jobId,
		  JSON.stringify({ status: "queued", job_id: jobId, created_at: job.created_at }),
		  { expirationTtl: KV_TTL }
		);
  
		// Fire-and-forget async processing
		ctx.waitUntil(processJob(job, env, request));
  
		return Response.json({ ok: true, job_id: jobId });
	  }
  
	  // GET /status/{job_id}
	  if (url.pathname.startsWith("/status/") && request.method === "GET") {
		const jobId = url.pathname.split("/")[2];
		const val = await env.y.get(jobId);
		if (!val) return new Response("Not found", { status: 404 });
		return new Response(val, { headers: { "content-type": "application/json" } });
	  }

	  // GET /download/:id?token=...
	  if (url.pathname.startsWith("/download/") && request.method === "GET") {
		const jobId = url.pathname.split("/")[2];
		const token = url.searchParams.get("token");
		const val = await env.y.get(jobId);
		if (!val) return new Response("Not found", { status: 404 });
		const jobState = JSON.parse(val);
		if (token !== jobState.download_token) {
		  return new Response("Unauthorized", { status: 401 });
		}
		const r2Key = jobState.r2_key;
		if (!r2Key) return new Response("Not found", { status: 404 });
		const obj = await env.ZIPS.get(r2Key);
		if (!obj) return new Response("Not found", { status: 404 });
		return new Response(obj.body, {
		  headers: {
		    "Content-Type": "application/zip",
		    "Content-Disposition": 'attachment; filename="etsy_pack_v1.zip"',
		    "Cache-Control": "no-store",
		  },
		});
	  }
  
	  return new Response("Not found", { status: 404 });
	},
  };
  
  async function processJob(job: any, env: any, request: Request) {
	const jobId = job.job_id;
	const payload = job.payload || {};
	const imageUrl = (payload.image_url || "").trim();

	if (imageUrl) {
	  const sizeCheck = await checkImageSize(imageUrl);
	  if (sizeCheck.error) {
		await env.y.put(
		  jobId,
		  JSON.stringify({ status: "error", job_id: jobId, http: 413, error: sizeCheck.error }),
		  { expirationTtl: KV_TTL }
		);
		return;
	  }
	}

	await env.y.put(
	  jobId,
	  JSON.stringify({ status: "running", job_id: jobId, started_at: Date.now() }),
	  { expirationTtl: KV_TTL }
	);
  
	try {
	  const res = await fetch("https://snaptosize-runner2.fly.dev/generate", {
		method: "POST",
		headers: {
		  "content-type": "application/json",
		  "authorization": `Bearer ${env.RUNNER_TOKEN}`,
		},
		body: JSON.stringify(job),
	  });
  
	  const text = await res.text();
  
	  if (!res.ok) {
		await env.y.put(
		  jobId,
		  JSON.stringify({ status: "error", job_id: jobId, http: res.status, error: text }),
		  { expirationTtl: KV_TTL }
		);
		return;
	  }

	  const runnerResult = safeJson(text);
	  const r2Key = runnerResult?.r2_key || runnerResult?.result?.r2_key;

	  let downloadUrl: string | null = null;
	  let downloadToken: string | null = null;
	  if (r2Key) {
		downloadToken = crypto.randomUUID();
		const origin = env.PUBLIC_BASE_URL || new URL(request.url).origin;
		downloadUrl = `${origin}/download/${jobId}?token=${downloadToken}`;
	  }

	  const jobState = {
		status: "done",
		job_id: jobId,
		finished_at: Date.now(),
		result: runnerResult,
		r2_key: r2Key ?? null,
		download_token: downloadToken,
		download_url: downloadUrl,
	  };

	  await env.y.put(jobId, JSON.stringify(jobState), { expirationTtl: KV_TTL });
	} catch (err: any) {
	  await env.y.put(
		jobId,
		JSON.stringify({ status: "error", job_id: jobId, error: String(err?.message || err) }),
		{ expirationTtl: KV_TTL }
	  );
	}
  }

  async function checkImageSize(imageUrl: string): Promise<{ error?: string }> {
	try {
	  const headRes = await fetch(imageUrl, { method: "HEAD" });
	  const cl = headRes.headers.get("Content-Length");
	  if (cl) {
		const n = parseInt(cl, 10);
		if (!isNaN(n) && n > MAX_SIZE_BYTES) {
		  return { error: "Image too large (max 25MB)" };
		}
		return {};
	  }
	  const getRes = await fetch(imageUrl);
	  if (!getRes.body) return {};
	  const reader = getRes.body.getReader();
	  let total = 0;
	  while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		total += value?.length ?? 0;
		if (total > MAX_SIZE_BYTES) {
		  reader.cancel();
		  return { error: "Image too large (max 25MB)" };
		}
	  }
	  return {};
	} catch (e: any) {
	  return { error: String(e?.message ?? e) };
	}
  }
  
  async function readBodyWithLimit(
	stream: ReadableStream<Uint8Array>,
	limit: number
  ): Promise<{ bytes: Uint8Array; overflow: boolean }> {
	const reader = stream.getReader();
	const chunks: Uint8Array[] = [];
	let total = 0;
	while (true) {
	  const { done, value } = await reader.read();
	  if (done) break;
	  const len = value?.length ?? 0;
	  total += len;
	  if (total > limit) {
		reader.cancel();
		return { bytes: new Uint8Array(0), overflow: true };
	  }
	  if (value) chunks.push(value);
	}
	const bytes = new Uint8Array(total);
	let offset = 0;
	for (const c of chunks) {
	  bytes.set(c, offset);
	  offset += c.length;
	}
	return { bytes, overflow: false };
  }

  function safeJson(s: string) {
	try {
	  return JSON.parse(s);
	} catch {
	  return s;
	}
  }
  
  
