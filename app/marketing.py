"""A2Z Marketing Center: AI-written ideas with owner-only review.

This is a draft/approval MVP. It deliberately does not publish externally.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from html import escape

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, select, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.auth import COOKIE_NAME, require_auth, require_auth_html
from app.db import Base, CatalogProduct, session_scope

router = APIRouter(prefix="/marketing", tags=["marketing"])
logger = logging.getLogger(__name__)


class MarketingDraft(Base):
    __tablename__ = "marketing_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(80))
    platforms_json: Mapped[str] = mapped_column(Text, default='["Facebook","Instagram"]')
    caption: Mapped[str] = mapped_column(Text)
    creative_brief: Mapped[str] = mapped_column(Text)
    source_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_creative_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MarketingWeeklyRun(Base):
    __tablename__ = "marketing_weekly_runs"
    week_key: Mapped[str] = mapped_column(String(12), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DraftEdit(BaseModel):
    caption: str = Field(min_length=1, max_length=6000)
    creative_brief: str = Field(min_length=1, max_length=3000)


class RevisionRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=1000)


class Decision(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    creative_id: int | None = Field(default=None, ge=1)


def serialize(d: MarketingDraft) -> dict:
    return {
        "id": d.id, "title": d.title, "category": d.category,
        "platforms": json.loads(d.platforms_json), "caption": d.caption,
        "creative_brief": d.creative_brief, "source_notes": d.source_notes,
        "status": d.status, "created_at": d.created_at.isoformat(),
        "approved_creative_id": d.approved_creative_id,
    }


SYSTEM_PROMPT = """You are the in-house marketing strategist for A2Z Aquariums, an Arizona independent
aquarium and exotic pet shop. Produce ORIGINAL polished, inviting marketing content.
Prioritize education, real animals, healthy husbandry, and reasons to visit the store.
Avoid synthetic-looking animal imagery: write a creative brief for REAL approved store photos/video.
Never claim a specific animal, product, sale, price, or event is currently available or confirmed
unless a source explicitly verifies it. Catalog records may be stale and do not prove stock.
Never invent discounts, giveaways, store hours, animal-care guarantees, or event dates.
No livestock shipping promises. Do not include turtles in visual briefs.
Write engaging human copy, not generic spam. Use hashtags sparingly.
Return ONLY valid JSON: {"posts":[{"title":"...","category":"education|product_spotlight|community|engagement",
"platforms":["Facebook","Instagram"],"caption":"...","creative_brief":"...","source_notes":"..."}]}.
Each post must have a distinct angle and factual, cautious messaging."""


async def suggest_posts(count: int, catalog_names: list[str]) -> list[dict]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(503, "OPENAI_API_KEY is not configured")
    user_prompt = (
        f"Create {count} different posts for the coming week. Balance engaging species care, "
        "product education, and local aquarium community. These are potential catalog examples "
        "ONLY, NOT verified inventory or confirmed availability: "
        + json.dumps(catalog_names[:24])
        + ". Do not imply in-stock status. Include useful on-image headline in creative_brief. "
          "No automatic publication. English."
    )
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": os.getenv("MARKETING_OPENAI_MODEL", "gpt-4.1-mini"),
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            payload = response.json()
            result = json.loads(payload["choices"][0]["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise HTTPException(502, "Marketing content generation failed") from exc
    posts = result.get("posts", [])
    if not isinstance(posts, list) or not posts:
        raise HTTPException(502, "AI returned no usable drafts")
    clean = []
    for post in posts[:count]:
        if not isinstance(post, dict):
            continue
        title = str(post.get("title", "")).strip()[:240]
        caption = str(post.get("caption", "")).strip()[:6000]
        brief = str(post.get("creative_brief", "")).strip()[:3000]
        if not title or not caption or not brief:
            continue
        platforms = post.get("platforms", ["Facebook", "Instagram"])
        platforms = [p for p in platforms if p in ("Facebook", "Instagram", "TikTok")] if isinstance(platforms, list) else []
        clean.append({
            "title": title,
            "category": str(post.get("category", "education"))[:80],
            "caption": caption, "creative_brief": brief,
            "platforms": platforms or ["Facebook", "Instagram"],
            "source_notes": str(post.get("source_notes", "AI concept; verify claims"))[:1000],
        })
    if not clean:
        raise HTTPException(502, "AI returned invalid draft fields")
    return clean


@router.get("", response_class=HTMLResponse)
async def marketing_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(PAGE_HTML)


@router.get("/api/drafts", dependencies=[Depends(require_auth)])
async def list_drafts():
    async with session_scope() as session:
        drafts = (await session.execute(
            select(MarketingDraft).order_by(MarketingDraft.created_at.desc(), MarketingDraft.id.desc()).limit(100)
        )).scalars().all()
        return {"data": [serialize(d) for d in drafts]}


async def _build_drafts():
    # Catalog names are idea prompts, never current-stock verification.
    async with session_scope() as session:
        names = (await session.execute(
            select(CatalogProduct.name).where(CatalogProduct.active.is_(True))
            .where(CatalogProduct.name.is_not(None))
            .order_by(CatalogProduct.synced_at.desc()).limit(100)
        )).scalars().all()
    posts = await suggest_posts(5, [n for n in names if n])
    async with session_scope() as session:
        rows = [
            MarketingDraft(title=p["title"], category=p["category"],
                           caption=p["caption"], creative_brief=p["creative_brief"],
                           platforms_json=json.dumps(p["platforms"]),
                           source_notes=p["source_notes"]) for p in posts
        ]
        session.add_all(rows)
        await session.flush()
        created = [serialize(d) for d in rows]
    # Automatically compose actual media for drafts when a suitable approved
    # store photo exists. Missing photos leave drafts pending; never fabricate
    # a mismatched animal image. A failed render must not discard written posts.
    from app.marketing_creative import _generate_creative
    for item in created:
        try:
            await _generate_creative(item["id"], None, "portrait")
        except HTTPException as exc:
            logger.warning("Marketing creative unavailable for draft %s: %s", item["id"], exc.detail)
        except Exception:
            logger.exception("Creative rendering failed for draft %s", item["id"])
    return created


@router.post("/api/generate", dependencies=[Depends(require_auth)])
async def generate_drafts():
    return {"data": await _build_drafts()}


@router.post("/api/weekly-run")
async def weekly_run(request: Request):
    """Called by a scheduler with a private shared secret; never publishes posts."""
    key = os.getenv("MARKETING_CRON_SECRET", "")
    incoming = request.headers.get("Authorization", "")
    if not key or not secrets.compare_digest(incoming, "Bearer " + key):
        raise HTTPException(401, "Unauthorized")
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    week_key = f"{year}-W{week:02d}"
    # Atomic run claim on Postgres prevents overlapping weekly generations.
    from sqlalchemy.dialects.postgresql import insert
    async with session_scope() as session:
        stmt = insert(MarketingWeeklyRun).values(
            week_key=week_key, status="running"
        ).on_conflict_do_nothing(index_elements=["week_key"]).returning(MarketingWeeklyRun.week_key)
        claimed = (await session.execute(stmt)).scalar_one_or_none()
    if claimed is None:
        return {"week": week_key, "result": "already_started"}
    try:
        created = await _build_drafts()
    except Exception:
        async with session_scope() as session:
            row = await session.get(MarketingWeeklyRun, week_key)
            row.status = "failed"
            row.error = "Generation failed; admin action needed to retry"
        raise
    async with session_scope() as session:
        row = await session.get(MarketingWeeklyRun, week_key)
        row.status = "completed"
    return {"week": week_key, "result": "created", "count": len(created)}


@router.put("/api/drafts/{draft_id}", dependencies=[Depends(require_auth)])
async def edit_draft(draft_id: int, payload: DraftEdit):
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        if draft.status != "pending":
            raise HTTPException(409, "Only pending drafts can be edited")
        draft.caption = payload.caption
        draft.creative_brief = payload.creative_brief
        await session.flush()
        return {"data": serialize(draft)}


@router.post("/api/drafts/{draft_id}/revise", dependencies=[Depends(require_auth)])
async def revise_draft(draft_id: int, payload: RevisionRequest):
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        if draft.status != "pending":
            raise HTTPException(409, "Only pending drafts can be revised")
        snapshot = {"title":draft.title,"caption":draft.caption,
                    "creative_brief":draft.creative_brief,"category":draft.category}
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "OPENAI_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": os.getenv("MARKETING_OPENAI_MODEL", "gpt-4.1-mini"),
                    "messages":[{"role":"system","content":SYSTEM_PROMPT},
                                {"role":"user","content":"Rewrite ONE existing draft based on the owner's request. "
                                  "Preserve factual safeguards and output the required JSON posts array of ONE post. "
                                  "Existing draft: " + json.dumps(snapshot) +
                                  "\\nOwner instruction: " + payload.instruction}],
                    "response_format":{"type":"json_object"}
                }
            )
            response.raise_for_status()
            revised = json.loads(response.json()["choices"][0]["message"]["content"])
            post = revised["posts"][0]
            caption = str(post["caption"]).strip()[:6000]
            brief = str(post["creative_brief"]).strip()[:3000]
            if not caption or not brief:
                raise ValueError("Missing text")
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise HTTPException(502, "Draft revision failed") from exc
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None or draft.status != "pending":
            raise HTTPException(409, "Draft no longer available for revision")
        draft.caption, draft.creative_brief = caption, brief
        await session.flush()
        return {"data": serialize(draft)}


@router.post("/api/drafts/{draft_id}/decision", dependencies=[Depends(require_auth)])
async def decide(draft_id: int, decision: Decision):
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        if draft.status != "pending":
            raise HTTPException(409, "Draft has already been reviewed")
        if decision.action == "approve":
            from app.marketing_creative import MarketingCreative, content_fingerprint
            if decision.creative_id is None:
                raise HTTPException(422, "Select and review a finished graphic or reel before approval")
            creative = await session.get(MarketingCreative, decision.creative_id)
            if creative is None or creative.draft_id != draft_id:
                raise HTTPException(422, "Creative not found for this draft")
            if creative.content_fingerprint != content_fingerprint(draft):
                raise HTTPException(409, "Content changed since rendering. Regenerate before approval")
            draft.approved_creative_id = creative.id
        draft.status = "approved" if decision.action == "approve" else "rejected"
        draft.reviewed_at = datetime.utcnow()
        await session.flush()
        # Approval does NOT trigger publishing; explicit publishing integration comes later.
        return {"data": serialize(draft), "published": False}


PAGE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A2Z Marketing Center</title><style>
:root{font:15px system-ui,sans-serif;background:#101b27;color:#eaf2f8}
body{margin:0;padding:24px}main{max-width:1040px;margin:auto}
a{color:#9bd0ff}.sub,small{color:#9ab0c2}p{line-height:1.45}
.card{border:1px solid #365166;background:#1b2d3d;border-radius:13px;padding:20px;margin:17px 0}
button{cursor:pointer;border:0;border-radius:7px;padding:10px 12px;margin:6px;font-weight:650;background:#b2dcf7;color:#102331}
button.approve{background:#57c59a}button.reject{background:#e78f9b}button:disabled{opacity:.5}
input,select{padding:10px;background:#263d4f;color:white;border:1px solid #648096;border-radius:6px;max-width:100%;margin:5px}
pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.tag{display:inline-block;border:1px solid #648096;border-radius:14px;padding:3px 9px;margin:3px;font-size:12px}
.preview{display:inline-flex;flex-direction:column;align-items:flex-start;gap:8px;border:1px solid #648096;padding:10px;border-radius:10px;margin:7px;vertical-align:top}
.preview img,.preview video{width:210px;max-width:100%;border-radius:6px}
#library img{height:55px;width:55px;object-fit:cover;vertical-align:middle;margin-right:8px}
</style></head><body><main>
<a href="/">← Back to A2Z tools</a><h1>A2Z Marketing Center</h1>
<p class="sub">AI plans the posts; approved real store photos become branded graphics and short photo reels. Review the complete post before approving. No publishing is connected.</p>
<section class="card"><h2>Store photo library</h2><p class="sub">Only upload photos you own or are licensed to use. Tags help the AI select suitable photographs. No synthetic animal images.</p>
<form id="upload"><input id="file" type="file" accept="image/jpeg,image/png,image/webp" required>
<input id="label" placeholder="Photo label" maxlength="180">
<input id="tags" placeholder="Tags: freshwater, corals, plants, betta..." maxlength="500">
<button type="submit">Add approved photo</button></form><div id="library"></div></section>
<button id="generate">Generate 5 new post ideas</button><p id="message" role="status"></p>
<section id="drafts"></section>
</main><script>
const msg=document.getElementById('message'),drafts=document.getElementById('drafts');
let media=[];
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
async function api(url,options){const res=await fetch(url,options),json=await res.json();if(!res.ok)throw Error(typeof json.detail==='string'?json.detail:'Operation failed');return json}
async function loadMedia(){
 const result=await api('/marketing/api/media');media=result.data;
 const root=document.getElementById('library');root.replaceChildren(node('p',media.length+' photos in library'));
 for(const m of media.slice(0,30)){const span=node('span',undefined,'tag'),img=node('img');img.src=m.url;img.alt=m.label;span.append(img,node('span',m.label));root.append(span)}
}
document.getElementById('upload').onsubmit=async function(e){
 e.preventDefault();const f=document.getElementById('file').files[0];if(!f)return;
 const data=new FormData();data.append('file',f);
 const label=document.getElementById('label').value,tags=document.getElementById('tags').value;
 try{await api('/marketing/api/media?label='+encodeURIComponent(label)+'&tags='+encodeURIComponent(tags),{method:'POST',body:data});
 this.reset();msg.textContent='Photo added.';await loadMedia();await refresh()}
 catch(err){msg.textContent=err.message}
};
async function refresh(){
 const data=await api('/marketing/api/drafts');drafts.replaceChildren();
 if(!data.data.length)drafts.append(node('p','No drafts yet. Generate ideas to begin.'));
 for(const d of data.data){
 const card=node('article',undefined,'card');card.append(node('h2',d.title));
 const meta=node('div');for(const m of [d.category,d.status,d.platforms.join(' + ')])meta.append(node('span',m,'tag'));card.append(meta);
 card.append(node('h3','Caption'),node('pre',d.caption),node('h3','Creative brief'),node('pre',d.creative_brief));
 card.append(node('small','Notes: '+d.source_notes));
 const gallery=node('div');card.append(gallery);
 async function refreshGallery(){
  const result=await api('/marketing/api/drafts/'+d.id+'/creatives');
  gallery.replaceChildren(node('h3','Finished creative previews'));
  if(!result.data.length)gallery.append(node('p','No media generated yet. Choose a tagged store photo and create a graphic.'));
  for(const item of result.data){
    const tile=node('div',undefined,'preview');
    if(item.mime==='video/mp4'){const vid=node('video');vid.src=item.url;vid.controls=true;tile.append(vid)}
    else{const img=node('img');img.src=item.url;img.alt=item.format+' A2Z branded graphic';tile.append(img)}
    tile.append(node('small',item.format+' creative #'+item.id));
    if(d.status==='pending'){
      const btn=node('button','Approve this finished post','approve');
      btn.onclick=async()=>{btn.disabled=true;try{await api('/marketing/api/drafts/'+d.id+'/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'approve',creative_id:item.id})});await refresh()}catch(err){msg.textContent=err.message;btn.disabled=false}};tile.append(btn)
    }gallery.append(tile)
  }
 }
 if(d.status==='pending'){
  const chooser=node('select'),opt=node('option','Automatic matching');opt.value='';chooser.append(opt);
  for(const m of media){const choice=node('option',m.label+' ('+m.tags+')');choice.value=m.id;chooser.append(choice)}
  card.append(node('h3','Create finished media'),chooser);
  for(const format of ['square','portrait','story','reel']){
   const btn=node('button','Generate '+format);
   btn.onclick=async()=>{btn.disabled=true;msg.textContent='Rendering '+format+'...';
   try{let u='/marketing/api/drafts/'+d.id+'/render?format='+format;if(chooser.value)u+='&media_id='+chooser.value;
   await api(u,{method:'POST'});await refreshGallery();msg.textContent='Creative ready for review.'}
   catch(err){msg.textContent=err.message}finally{btn.disabled=false}};card.append(btn)
  }
  card.append(node('h3','Make changes'));
  const edit=node('button','Edit caption / brief');
  edit.onclick=async()=>{const caption=prompt('Edit caption',d.caption);if(caption===null)return;
  const creative_brief=prompt('Edit creative brief',d.creative_brief);if(creative_brief===null)return;
  try{await api('/marketing/api/drafts/'+d.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({caption,creative_brief})});await refresh()}
  catch(err){msg.textContent=err.message}};card.append(edit);
  const revise=node('button','Ask AI to revise');
  revise.onclick=async()=>{const instruction=prompt('What would you like changed?');if(!instruction)return;
  revise.disabled=true;try{await api('/marketing/api/drafts/'+d.id+'/revise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction})});await refresh()}
  catch(err){msg.textContent=err.message;revise.disabled=false}};card.append(revise);
  const reject=node('button','Reject','reject');
  reject.onclick=async()=>{try{await api('/marketing/api/drafts/'+d.id+'/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'reject'})});await refresh()}
  catch(err){msg.textContent=err.message}};card.append(reject);
 }
 drafts.append(card);refreshGallery().catch(err=>msg.textContent=err.message)
 }
}
document.getElementById('generate').onclick=async function(){
 this.disabled=true;msg.textContent='Generating ideas...';
 try{await api('/marketing/api/generate',{method:'POST'});await refresh();msg.textContent='New posts are ready for creative production.'}
 catch(err){msg.textContent=err.message}finally{this.disabled=false}
};
loadMedia().then(refresh).catch(err=>msg.textContent=err.message);
</script></body></html>"""