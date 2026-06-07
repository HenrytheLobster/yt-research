"""
build_taxonomy.py — Catalog the automations people TEACH (breadth, not convergence).
Buckets clean qwen3 tactic extractions into automation categories and counts how many
distinct videos teach each one. Writes data/automations_taxonomy.json (+ .js) for the dashboard.

Goal: survey the landscape. "How many people talk about X" is the signal — NOT picking a winner.
"""
import glob, json, re, collections
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
EXTRACTED = ROOT / "data" / "youtube" / "extracted"
OUT = ROOT / "data" / "automations_taxonomy.json"

# Each category: (key, display name, description, is_watch_idea, [keywords])
# is_watch_idea = one of Mike's hypotheses we explicitly want to see coverage for.
CATEGORIES = [
 ("voice_call","AI Front Desk / Voice & Calls","Answers calls, qualifies, books — voice agents & missed-call response.",False,
   ["voice agent","voice receptionist","answer the phone","answers calls","phone call","missed call","call handling","inbound call","outbound call","receptionist","ai front desk","ivr","cold call"]),
 ("chatbot","Website / Messaging Chatbot","Chat widgets & messaging bots answering questions and capturing contacts.",False,
   ["chatbot","chat bot","chat widget","website chat","messaging bot","support bot","unified inbox","live chat"]),
 ("lead_qual","Lead Capture & Qualification","Qualifying, scoring, and routing inbound leads before a human touches them.",False,
   ["lead qualif","qualify lead","qualifying","pre-qualif","prequalif","lead capture","capture lead","lead gen","lead scoring","lead routing","conflict check"]),
 ("booking","Appointment Booking & Scheduling","Booking, rescheduling, reminders, no-show backfill, calendar sync.",False,
   ["appointment","booking","book a","schedule appointment","scheduling","calendar","no-show","no show","reschedul","reminder","recall"]),
 ("intake_plan","Customer Intake & Service Plans","Intake forms that produce a tailored plan/proposal for the customer.",True,
   ["intake form","service plan","treatment plan","build a plan","custom plan","questionnaire","intake agent","client intake","patient intake","scope"]),
 ("onboarding","Client Onboarding Workflow","Form-to-CRM onboarding: welcome emails, folders, contracts, handoff.",False,
   ["onboarding","welcome email","kickoff","contract draft","proposal","handoff","client folder"]),
 ("seo_content","SEO & Content Engine","Keyword/GSC-driven blog & content generation, optimization, publishing.",True,
   ["seo","search console","gsc","blog","content engine","content workflow","keyword","crosslink","cross-link","internal link","publish","article","metadata","content optimiz","aeo"]),
 ("social","Social Media Automation","Generate posts, schedule, cross-post, review performance, iterate.",True,
   ["social media","linkedin","instagram","facebook post","twitter","tiktok","caption","cross-post","cross post","content calendar","social post","post daily","hootsuite","sprout"]),
 ("invoice_ap","Invoice / AP / Expense Processing","Read invoices/receipts, extract fields, route to sheet/AP.",False,
   ["invoice","receipt","accounts payable","bill pay","expense","ap chasing","procure"]),
 ("books_tax","Bookkeeping / Accounting / Tax","Categorization, reconciliation, month-end close, tax prep.",False,
   ["bookkeep","ledger","reconcil","month-end","month end","tax","categoriz transaction","quickbooks","general ledger","payroll","audit"]),
 ("email","Email Automation & Triage","Classify, draft, route, and follow-up on email.",False,
   ["email","inbox","classify email","email classif","follow-up email","followup email","draft email","email nurtur","cold email","email outreach","gmail"]),
 ("ops_pipeline","Operations / Job Scheduling Pipeline","Ingest jobs and schedule/dispatch by duration, route, capacity.",True,
   ["dispatch","job schedul","route job","work order","completion time","capacity planning","staff schedul","logistics","eta","job queue"]),
 ("reviews","Review & Reputation Management","Solicit, monitor, and respond to reviews; reputation flywheel.",False,
   ["review","reputation","google my business","gmb","testimonial","feedback request"]),
 ("docproc","Document Processing & Extraction","Watch-folder/contract/PDF ingestion, OCR, structured extraction.",False,
   ["document","contract","ocr","extract","pdf","data entry","transpose","watch folder","discovery document"]),
 ("crm_data","CRM & Data Centralization","Sync/centralize data across tools into a CRM or single source.",False,
   ["crm","hubspot","gohighlevel","go high level","highlevel","airtable","centraliz","single database","pipe data","aggregat"]),
 ("knowledge","Internal Knowledge / Research Assistant","Knowledge bases, research reports, SOP generation, dictation.",False,
   ["knowledge base","notebook lm","research report","sop","documentation","dictation","summariz","brief","deep research"]),
]

def load(path):
    t=open(path,encoding="utf-8",errors="ignore").read().split("\x00")[0]
    t=t[:t.rfind("}")+1]
    try:
        return json.loads(t)
    except Exception:
        m=re.search(r'\{.*\}',t,re.DOTALL)
        try:
            return json.loads(m.group()) if m else None
        except Exception:
            return None

cat_videos   = {k:set() for k,*_ in CATEGORIES}
cat_tools    = {k:collections.Counter() for k,*_ in CATEGORIES}
cat_examples = {k:[] for k,*_ in CATEGORIES}
total_videos=set()

for f in glob.glob(str(EXTRACTED/"*.json")):
    d=load(f)
    if not d or not str(d.get("model_used","")).startswith("qwen3"):
        continue
    vid=d.get("video_id",""); title=d.get("title","")
    total_videos.add(vid)
    for t in d.get("tactics",[]):
        name=(t.get("name") or "").strip()
        desc=(t.get("description") or "").strip()
        tools=[x.strip() for x in (t.get("tools_mentioned") or []) if x and x.strip()]
        q=(t.get("source_quotes") or [""])[0]
        blob=(name+" "+desc+" "+str(t.get("trigger",""))+" "+" ".join(tools)+" "+title).lower()
        for key,nm,dsc,watch,kws in CATEGORIES:
            if any(k in blob for k in kws):
                cat_videos[key].add(vid)
                for tl in tools:
                    cat_tools[key][tl]+=1
                if len(cat_examples[key])<6 and len(desc)>40 and len(q)>30:
                    cat_examples[key].append({"name":name,"desc":desc[:170],
                        "quote":q[:160],"source":title[:70],"video_id":vid})

cats=[]
for key,nm,dsc,watch,kws in CATEGORIES:
    cats.append({
        "key":key,"name":nm,"desc":dsc,"watch":watch,
        "video_count":len(cat_videos[key]),
        "top_tools":[{"tool":t,"n":n} for t,n in cat_tools[key].most_common(8)],
        "examples":cat_examples[key],
    })
cats.sort(key=lambda c:-c["video_count"])

payload={
    "generated_at":datetime.now().isoformat(),
    "model":"qwen3:8b",
    "total_videos":len(total_videos),
    "categories":cats,
}
OUT.write_text(json.dumps(payload,indent=2))
# also emit a JS file so the dashboard works on a plain double-click (no server/CORS)
OUT.with_suffix(".js").write_text("window.TAXONOMY = "+json.dumps(payload)+";")

print("Wrote taxonomy JSON + JS. Total videos: "+str(len(total_videos)))
print(format("CATEGORY","<38")+format("VIDEOS",">7")+"  watch")
for c in cats:
    star = "*" if c["watch"] else ""
    print(format(c["name"],"<38")+format(c["video_count"],">7")+"  "+star)
