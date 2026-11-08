"""Offline desktop-control behavior; no Hermes, provider, DOM injection or paid calls.

A small hook runner executes the real plain-ESM components and their event handlers.
The browser harness under work/ additionally verifies layout with Hermes's React.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

CASES = [
    "read-only expansion never starts paid work",
    "anonymous review hides route metadata and records a choice",
    "explicit reveal disables blind preference",
    "revealed evidence distinguishes missing checks and served routes",
    "rhythm preview needs explicit consent and invalidates on edits",
    "rhythm fallback exports a reviewable command",
    "rhythm undo uses the current configuration fingerprint",
    "auction requires declared candidates and a budget preview",
    "auction sends frozen input and explicit estimated-cap consent",
    "auction retains failed and skipped routes with unknown cost",
    "model discovery failure remains distinct from no connections",
    "reread changes are explicit and chat-scoped",
    "late reread responses cannot overwrite another chat",
    "delegation consent is separate and never activates a backend",
    "blocked rhythm proposals never expose a schedule command",
    "auction discovery survives edits while it loads",
    "qualified receipt totals exclude historical counterfactuals",
    "heatmap keyboard inspection uses one tab stop",
    "manual routes stay declarations and preserve arbitrary identifiers",
    "empty or failed inventory still offers manual route entry",
    "pricing cards keep unknown values and explicit zero distinct",
    "pricing acknowledgement resets when a tier or rate changes",
    "saved unknown tiers are never presented as no tiers",
    "unguarded dispatch cannot enable autonomous modes",
    "host-tool plans require an explicit mode choice",
    "unavailable replay never advertises stale projected savings",
    "native source preview requires review and resets approval on edits",
    "native source runtime updates require explicit source review",
    "native source undo requires current configuration and explicit approval",
    "observation starts only through its explicit control",
    "compatibility explains normal host-tool file plans",
    "unsupported unchanged policies expose only a run-always reset",
    "host-tool mode never offers automatic unchanged cycling",
    "chat projections identify current route rates and historical repricing",
    "helper cache upper bounds remain distinct from unknown costs",
    "reasoning stays fixed and desktop imports remain public",
]

JS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
let active;
const hooks = {
  useState(initial) {
    const owner = active, index = owner.cursor++;
    if (!(index in owner.slots)) owner.slots[index] = typeof initial === 'function' ? initial() : initial;
    return [owner.slots[index], value => {
      const next = typeof value === 'function' ? value(owner.slots[index]) : value;
      if (!Object.is(next, owner.slots[index])) { owner.slots[index] = next; owner.dirty = true; }
    }];
  },
  useRef(initial) {
    const index = active.cursor++;
    if (!(index in active.slots)) active.slots[index] = {current: initial};
    return active.slots[index];
  },
  useEffect(effect, deps) {
    const owner = active, index = owner.cursor++, previous = owner.slots[index];
    if (!previous || !deps || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
      owner.slots[index] = {deps};
      owner.effects.push(() => {
        if (previous && previous.cleanup) previous.cleanup();
        owner.slots[index].cleanup = effect();
      });
    }
  },
  useCallback(callback, deps) {
    const index = active.cursor++, previous = active.slots[index];
    if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) active.slots[index] = {deps, callback};
    return active.slots[index].callback;
  }
};
const jsx = (type, props = {}, key) => ({type, props, key});
const sdk = {Codicon: () => null, ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', host: {state: {}, notify: () => {}}, useValue: () => null};
const sandbox = {...hooks, ...sdk, jsx, jsxs: jsx, Intl, Date, URLSearchParams, console};
vm.createContext(sandbox);
const code = source.replace(/^import .* from .*\n/gm, '').replace('export default {', 'const desktopPlugin = {');
vm.runInContext(code + '\nthis.parts={BlindTasteTest,RhythmHeatmap,RhythmControl,ModelAuction,AuctionResult,RereadControls,DelegationControls,NativeCronControl,JobEvidence,ReceiptSummary,Hero,RouteEntry,PricingCard,ModelPicker,CompatibilityCard,LiveOptions,JobBubble,ThisChat,promotionTarget,canUseMode};', sandbox);
const plain = value => JSON.parse(JSON.stringify(value));
function mount(name, props) {
  const owner = {slots: [], effects: [], dirty: true, cursor: 0, tree: null, props};
  owner.render = () => {
    let attempts = 0;
    do {
      assert.ok(++attempts < 30, `render loop in ${name}`);
      owner.dirty = false; owner.cursor = 0; active = owner;
      owner.tree = sandbox.parts[name](owner.props);
      active = null;
      const effects = owner.effects.splice(0); effects.forEach(effect => effect());
    } while (owner.dirty);
    return owner.tree;
  };
  owner.flush = async () => {
    for (let i = 0; i < 8; i++) { await Promise.resolve(); if (owner.dirty) owner.render(); }
    await new Promise(resolve => setImmediate(resolve));
    if (owner.dirty) owner.render();
  };
  owner.rerender = props => {owner.props = props; owner.dirty = true; owner.render();};
  owner.dispose = () => owner.slots.forEach(slot => {if (slot && slot.cleanup) slot.cleanup();});
  owner.render();
  return owner;
}
function nodes(value, found = []) {
  if (Array.isArray(value)) value.forEach(child => nodes(child, found));
  else if (value && typeof value === 'object') {found.push(value); if (value.props) nodes(value.props.children, found);}
  return found;
}
function text(value) {
  if (Array.isArray(value)) return value.map(text).join(' ');
  if (value && typeof value === 'object') return text(value.props && value.props.children);
  return value == null || value === false ? '' : String(value);
}
const all = owner => nodes(owner.tree);
const button = (owner, name) => all(owner).find(node => node.type === 'button' && text(node) === name);
const click = (owner, name) => {
  const control = button(owner, name); assert.ok(control, `Missing button: ${name}`);
  assert.equal(!!control.props.disabled, false, `Disabled button: ${name}`);
  control.props.onClick(); owner.render();
};
const input = (owner, title) => {
  const label = all(owner).find(node => node.type === 'label' && text(node).trim().startsWith(title));
  assert.ok(label, `Missing field: ${title}`);
  const field = nodes(label).find(node => ['input', 'select', 'textarea'].includes(node.type));
  assert.ok(field, `Missing input: ${title}`); return field;
};
const change = (owner, title, value) => {
  const control = input(owner, title); assert.equal(!!control.props.disabled, false, `Disabled field: ${title}`);
  control.props.onChange({target: {value, checked: value}}); owner.render();
};
function deferred() {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};}
const basePolicy = {enabled:false,writes:false,results:false,batching:false,reasoning_unchanged:true};
const inventory = {providers:[{slug:'cloud-one',name:'Cloud One',models:[{id:'model/a'},{id:'model/b'},{id:'model/c'}]},{slug:'cloud-two',name:'Cloud Two',models:[{id:'another/route'}]}]};
const rhythm = {ok:true,job_id:'job-a',timezone:'UTC',config_fingerprint:'fingerprint-current',current_interval_minutes:30,
  heatmap:[],evidence:{complete_observations:14,comparisons:13,span_days:8},recommendation:{interval_minutes:60,reason:'Review a longer interval.'},control:{supported:true,can_undo:true}};
function makeContext(overrides = {}) {
  const calls = [];
  const ctx = {rest(path, options = {}) {
    const base = path.split('?')[0], call = {path, base, method:options.method || 'GET', body:options.body}; calls.push(call);
    if (overrides[`${call.method} ${base}`]) return Promise.resolve(overrides[`${call.method} ${base}`](call));
    if (base === '/chat-models') return Promise.resolve(inventory);
    if (base === '/auction') return Promise.resolve({ok:true,auctions:[]});
    if (base === '/reread') return Promise.resolve({ok:true,policy:{...basePolicy}});
    if (base === '/rhythm') return Promise.resolve({...rhythm});
    if (base === '/delegation') return Promise.resolve({ok:true,enabled:false});
    if (base === '/receipt') return Promise.resolve({sample_count:0});
    if (base === '/compare') return Promise.resolve({available:true,execution_id:'run-a',latest_blind:null});
    if (base === '/contract') return Promise.resolve({ok:true,contract:{}});
    return Promise.resolve({ok:true});
  }};
  return {ctx,calls,posts:() => calls.filter(call => call.method === 'POST')};
}
const answers = {id:'comparison-a',status:'completed',samples:[{sample_index:0,A:{answer:'Answer alpha',status:'completed'},B:{answer:'Answer beta',status:'completed'}}],can_choose:true,choice:null,revealed:false};
const results = [];
async function test(name, run) {try {await run();results.push({name,ok:true});} catch(error) {results.push({name,ok:false,error:error.stack});}}
(async () => {
await test('read-only expansion never starts paid work', async () => {
  for (const [name, title] of [['JobEvidence','▸ Evidence and controls'],['RhythmControl','▸ Rhythm · schedule frequency'],['ModelAuction','▸ Model auction · compare declared routes']]) {
    const api = makeContext(); const view = mount(name, {ctx:api.ctx,jobId:'job-a',item:{job_id:'job-a'},onChanged:() => {}});
    await view.flush(); assert.equal(api.calls.length,0); click(view,title); await view.flush(); assert.ok(api.calls.length); assert.equal(api.posts().length,0); view.dispose();
  }
});
await test('anonymous review hides route metadata and records a choice', async () => {
  const revealed = {...answers,choice:'A',revealed:true,can_choose:false,reveal:{experiment_label:'Model comparison',samples:[]}};
  const api = makeContext({'POST /blind-choice':() => ({ok:true,comparison:revealed})});
  const view = mount('BlindTasteTest',{ctx:api.ctx,comparison:{...answers,reveal:{private_model:'DO-NOT-REVEAL'}}});
  await view.flush(); assert.ok(text(view.tree).includes('Answer alpha')); assert.ok(text(view.tree).includes('Answer beta'));
  assert.ok(!text(view.tree).includes('DO-NOT-REVEAL')); assert.equal(api.posts().length,0);
  click(view,'Prefer A'); await view.flush(); assert.deepEqual(plain(api.posts()[0].body),{comparison_id:'comparison-a',choice:'A'});
  assert.ok(text(view.tree).includes('Your choice: Prefer A')); assert.equal(button(view,'Prefer B'),undefined); view.dispose();
});
await test('explicit reveal disables blind preference', async () => {
  const api = makeContext({'POST /blind-reveal':() => ({ok:true,comparison:{...answers,revealed:true,can_choose:false,choice_eligible:false,reveal:{samples:[]}}})});
  const view = mount('BlindTasteTest',{ctx:api.ctx,comparison:answers}); await view.flush();
  click(view,'Reveal models, costs and checks'); await view.flush();
  assert.deepEqual(plain(api.posts()[0].body),{comparison_id:'comparison-a'});
  assert.ok(text(view.tree).includes('does not count as a blind preference')); assert.equal(button(view,'Prefer A'),undefined); view.dispose();
});
await test('revealed evidence distinguishes missing checks and served routes', async () => {
  const view = mount('BlindTasteTest',{ctx:makeContext().ctx,comparison:{...answers,revealed:true,can_choose:false,reveal:{experiment_label:'Model comparison',samples:[{A:{requested:{route:'main'},answered_by:{provider:'actual-provider',model:'actual-model'},cost:{actual_usd:0},validation:{ok:true,configured:false}},B:{cost:{actual_usd:null,estimated_usd:null},validation:{ok:false,configured:true}}}]}}});
  await view.flush(); const content = text(view.tree);
  assert.ok(content.includes('actual-provider / actual-model')); assert.ok(content.includes('No task-specific checks configured'));
  assert.ok(content.includes('$0.00 recorded')); assert.ok(content.includes('Unknown cost')); view.dispose();
});
await test('rhythm preview needs explicit consent and invalidates on edits', async () => {
  const api = makeContext({'POST /rhythm-preview':call => ({...rhythm,interval_minutes:call.body.interval_minutes,can_apply:true,projection:{horizon_days:30,current_scheduled_invocations:1440,proposed_scheduled_invocations:720,estimated_avoided_usd:0.5}}),'POST /rhythm':() => ({ok:true,applied:true,message:'Applied.'})});
  const view = mount('RhythmControl',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Rhythm · schedule frequency'); await view.flush();
  assert.equal(input(view,'This job is a noncritical').props.checked,false); change(view,'This job is a noncritical',true); change(view,'Maximum acceptable delay','120');
  click(view,'Preview frequency change'); await view.flush(); assert.equal(button(view,'Apply this schedule').props.disabled,true);
  change(view,'I approve running this job',true); change(view,'Proposed frequency',6); assert.equal(button(view,'Apply this schedule'),undefined);
  click(view,'Preview frequency change'); await view.flush(); assert.equal(button(view,'Apply this schedule').props.disabled,true);
  change(view,'I approve running this job',true); click(view,'Apply this schedule'); await view.flush();
  const body = api.posts().find(call => call.base === '/rhythm').body;
  assert.equal(body.confirmed,true); assert.equal(body.noncritical,true); assert.equal(body.allowed_staleness_minutes,120); assert.equal(body.expected_config_fingerprint,'fingerprint-current');
  assert.equal(api.posts().filter(call => call.base === '/rhythm').length,1); view.dispose();
});
await test('rhythm fallback exports a reviewable command', async () => {
  const report = {...rhythm,control:{supported:false,can_undo:false,mode:'export_only',reason:'No verified CLI.'}};
  const api = makeContext({'GET /rhythm':() => report,'POST /rhythm-preview':() => ({...report,can_apply:false,can_export:true,projection:{horizon_days:30},export:{argv:['hermes','cron','edit','job-a','--schedule','every 60m']}})});
  const view = mount('RhythmControl',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Rhythm · schedule frequency'); await view.flush(); click(view,'Preview frequency change'); await view.flush();
  assert.equal(button(view,'Apply this schedule'),undefined); assert.ok(text(view.tree).includes("hermes cron edit job-a --schedule 'every 60m'"));
  assert.ok(text(view.tree).includes('has not changed the job')); assert.equal(api.posts().some(call => call.base === '/rhythm'),false); view.dispose();
});
await test('rhythm undo uses the current configuration fingerprint', async () => {
  const api = makeContext({'POST /rhythm-undo':() => ({ok:true,undone:true})});
  const view = mount('RhythmControl',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Rhythm · schedule frequency'); await view.flush(); click(view,'Undo Rhythm schedule change'); await view.flush();
  assert.deepEqual(plain(api.posts()[0].body),{job_id:'job-a',confirmed:true,expected_config_fingerprint:'fingerprint-current'}); view.dispose();
});
async function auctionView(api) {
  const view = mount('ModelAuction',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Model auction · compare declared routes'); await view.flush();
  change(view,'model/a',true); change(view,'model/b',true); change(view,'Total cost cap','1'); return view;
}
const auctionPreview = {ok:true,available:true,models:[{},{}],execution_id:'execution-frozen',input_sha256:'sha-frozen',cost_cap_usd:1,estimated_reservation_usd:0.1};
await test('auction requires declared candidates and a budget preview', async () => {
  const api = makeContext({'POST /auction-preview':() => auctionPreview}); const view = mount('ModelAuction',{ctx:api.ctx,jobId:'job-a'});
  click(view,'▸ Model auction · compare declared routes'); await view.flush();
  assert.equal(button(view,'Preview auction budget').props.disabled,true); assert.equal(input(view,'Use an estimated cap').props.checked,false);
  change(view,'model/a',true); change(view,'model/b',true); assert.equal(button(view,'Preview auction budget').props.disabled,true);
  change(view,'Total cost cap','1'); click(view,'Preview auction budget'); await view.flush();
  assert.equal(button(view,'Run paid model auction').props.disabled,true); change(view,'I approve paid calls',true);
  change(view,'Total cost cap','2'); assert.equal(button(view,'Run paid model auction'),undefined);
  assert.equal(api.posts().filter(call => call.base === '/auction').length,0); assert.equal(api.posts()[0].body.accept_estimated_cap,false); view.dispose();
});
await test('auction sends frozen input and explicit estimated-cap consent', async () => {
  const api = makeContext({'POST /auction-preview':() => auctionPreview,'POST /auction':() => ({ok:true,auction:{id:'auction-a',status:'completed',scoreboard:[],calls:[]}})});
  const view = await auctionView(api); change(view,'Use an estimated cap',true); click(view,'Preview auction budget'); await view.flush(); change(view,'I approve paid calls',true); click(view,'Run paid model auction'); await view.flush();
  const body = api.posts().find(call => call.base === '/auction').body;
  assert.deepEqual(plain(body),{job_id:'job-a',models:[{provider:'cloud-one',model:'model/a'},{provider:'cloud-one',model:'model/b'}],cost_cap_usd:1,max_tokens:1024,accept_estimated_cap:true,confirmed:true,execution_id:'execution-frozen',expected_input_sha256:'sha-frozen'});
  assert.equal(api.posts().filter(call => call.base === '/auction').length,1); view.dispose();
});
await test('auction retains failed and skipped routes with unknown cost', async () => {
  const record = {id:'auction-failed',status:'stopped_unknown_spend',scoreboard:[{index:0,provider:'cloud-one',model:'model/a',status:'failed',cost:{actual_usd:null,estimated_usd:null},structural_checks:{ok:false,configured:true}},{index:1,provider:'cloud-one',model:'model/b',status:'skipped',cost:null}],calls:[{index:0,status:'failed',error:'Failure retained',requested:{provider:'cloud-one',model:'model/a'}}]};
  const api = makeContext({'POST /auction-preview':() => auctionPreview,'POST /auction':() => ({ok:false,auction:record,message:'Stopped.'})});
  const view = await auctionView(api); click(view,'Preview auction budget'); await view.flush(); change(view,'I approve paid calls',true); click(view,'Run paid model auction'); await view.flush();
  const result = all(view).find(node => node.type === sandbox.parts.AuctionResult); assert.ok(result); assert.deepEqual(plain(result.props.auction),record);
  const receipt = mount('AuctionResult',{auction:result.props.auction,ctx:api.ctx}); await receipt.flush(); const content=text(receipt.tree);
  assert.ok(content.includes('failed')); assert.ok(content.includes('skipped')); assert.ok(content.includes('Unknown cost')); assert.ok(content.includes('Calls already made may still be charged'));
  assert.equal(api.posts().filter(call => call.base === '/auction').length,1); view.dispose(); receipt.dispose();
});
await test('model discovery failure remains distinct from no connections', async () => {
  const api = makeContext({'GET /chat-models':() => Promise.reject(new Error('offline'))});
  const view = mount('ModelAuction',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Model auction · compare declared routes'); await view.flush();
  assert.ok(text(view.tree).includes('Configured-route discovery failed')); assert.ok(!text(view.tree).includes('No configured or declared model choices were returned')); assert.equal(api.posts().length,0); view.dispose();
});
await test('reread changes are explicit and chat-scoped', async () => {
  const api = makeContext({'POST /reread':call => ({ok:true,policy:call.body})});
  const view = mount('RereadControls',{ctx:api.ctx,sessionId:'chat-a',chatEnabled:true}); await view.flush();
  assert.equal(input(view,'Enable re-read').props.checked,false); change(view,'Enable re-read',true); change(view,'Completed file-write',true); change(view,'Suggest batching',true);
  assert.equal(api.posts().length,0); click(view,'Save re-read choices'); await view.flush();
  assert.deepEqual(plain(api.posts()[0].body),{session_id:'chat-a',enabled:true,writes:true,results:false,batching:true});
  assert.ok(text(view.tree).includes('Reasoning text is preserved in full')); assert.equal(button(view,'Save re-read choices').props.disabled,true); view.dispose();
});
await test('late reread responses cannot overwrite another chat', async () => {
  const slow = deferred();
  const api = makeContext({'GET /reread':call => call.path.includes('chat-a') ? slow.promise : {ok:true,policy:{...basePolicy}}});
  const view = mount('RereadControls',{ctx:api.ctx,sessionId:'chat-a',chatEnabled:true});
  view.rerender({ctx:api.ctx,sessionId:'chat-b',chatEnabled:false}); await view.flush();
  slow.resolve({ok:true,policy:{enabled:true,writes:true,results:true,batching:true}}); await view.flush();
  assert.equal(input(view,'Enable re-read').props.checked,false); change(view,'Enable re-read',true); click(view,'Save re-read choices'); await view.flush();
  assert.equal(api.posts()[0].body.session_id,'chat-b'); assert.equal(api.posts()[0].body.writes,false); view.dispose();
});
await test('delegation consent is separate and never activates a backend', async () => {
  const api = makeContext({'POST /delegation':call => ({ok:true,...call.body})});
  const view = mount('DelegationControls',{ctx:api.ctx,picked:{provider:'cloud-one',model:'model/a'}}); await view.flush(); assert.equal(api.calls.length,0);
  click(view,'▸ Optional delegation backend'); await view.flush(); change(view,'Permit this selected helper',true);
  assert.equal(button(view,'Save profile delegation consent').props.disabled,true); assert.equal(api.posts().length,0);
  change(view,'I approve saving this profile-wide',true); click(view,'Save profile delegation consent'); await view.flush();
  assert.deepEqual(plain(api.posts()[0].body),{enabled:true,provider:'cloud-one',model:'model/a',confirmed:true});
  assert.ok(text(view.tree).includes('Saving consent does not select the backend')); assert.equal(api.posts().length,1); view.dispose();
});

await test('blocked rhythm proposals never expose a schedule command', async () => {
  const api = makeContext({'POST /rhythm-preview':() => ({...rhythm,can_apply:false,can_export:false,blockers:['This action job is excluded.'],projection:{horizon_days:30},export:{argv:['hermes','cron','edit','job-a','--schedule','every 60m']}})});
  const view = mount('RhythmControl',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Rhythm · schedule frequency'); await view.flush(); click(view,'Preview frequency change'); await view.flush();
  assert.ok(text(view.tree).includes('This action job is excluded.')); assert.ok(!text(view.tree).includes('hermes cron edit')); assert.equal(button(view,'Apply this schedule'),undefined); view.dispose();
});
await test('auction discovery survives edits while it loads', async () => {
  const slow = deferred(); const api = makeContext({'GET /chat-models':() => slow.promise});
  const view = mount('ModelAuction',{ctx:api.ctx,jobId:'job-a'}); click(view,'▸ Model auction · compare declared routes');
  change(view,'Total cost cap','1'); slow.resolve(inventory); await view.flush();
  assert.ok(input(view,'model/a')); assert.equal(input(view,'Total cost cap').props.value,'1'); assert.equal(api.posts().length,0); view.dispose();
});
await test('qualified receipt totals exclude historical counterfactuals', async () => {
  const view = mount('ReceiptSummary',{receipt:{sample_count:4,preferred_basis:'estimated',net_payback_usd:999,historical_savings_usd:1000,qualified_efficiency_savings_usd:null,qualified_net_payback_usd:null,efficiency_savings_proven:false,cost_bases:{estimated:{historical_savings_usd:1000,overhead_usd:1,unproven_settings_comparison_count:4}}}});
  await view.flush(); const tiles=all(view).filter(node=>node.type && node.type.name==='Tile');
  assert.equal(tiles.find(node=>node.props.title==='Proven saving').props.value,'Unknown');
  assert.equal(tiles.find(node=>node.props.title==='After costs').props.value,'Unknown');
  assert.ok(text(view.tree).includes('Historical counterfactual: $1000.00')); assert.ok(text(view.tree).includes('Efficiency savings are not established')); view.dispose();
});
await test('heatmap keyboard inspection uses one tab stop', async () => {
  const view = mount('RhythmHeatmap',{timezone:'UTC',heatmap:[{weekday:1,hour:1,observations:3,comparisons:2,changes:1}]});
  const group=all(view).find(node=>node.type==='div' && node.props.role==='group'); assert.equal(group.props.tabIndex,0);
  assert.ok(all(view).filter(node=>node.type==='button').every(node=>node.props.tabIndex===-1));
  const press=key=>{const current=all(view).find(node=>node.type==='div' && node.props.role==='group');current.props.onKeyDown({key,preventDefault:()=>{}});view.render();};
  press('ArrowDown'); press('ArrowRight'); assert.ok(text(view.tree).includes('Tue 01:00: 3 observations, 2 comparisons, 1 changes')); view.dispose();
});

await test('manual routes stay declarations and preserve arbitrary identifiers', async () => {
  const api = makeContext({'POST /model-route':call => ({ok:true,configured_route:call.body,activated:false})});
  const view = mount('RouteEntry',{ctx:api.ctx}); await view.flush(); assert.equal(api.calls.length,0);
  click(view,'▸ Add a provider / model route'); change(view,'Provider identifier','new-cloud.route'); change(view,'Model identifier','tenant/never-listed-model:version-2027');
  assert.equal(api.posts().length,0); click(view,'Add route choice'); await view.flush();
  assert.deepEqual(plain(api.posts()[0].body),{provider:'new-cloud.route',model:'tenant/never-listed-model:version-2027'});
  assert.equal(api.posts().length,1); assert.equal(api.posts().some(call=>call.base==='/chat-model'),false);
  assert.ok(text(view.tree).includes('Select it separately')); assert.ok(text(view.tree).includes('Authentication is checked by Hermes')); view.dispose();
});
await test('empty or failed inventory still offers manual route entry', async () => {
  for(const inventory of [{providers:[],authentication_verified:false}, {ok:false,error:'Inventory unavailable'}]){
    const api=makeContext({'GET /chat-models':()=>inventory}); const view=mount('ModelPicker',{ctx:api.ctx,picked:null,onPicked:()=>{}}); await view.flush();
    assert.ok(all(view).find(node=>node.type===sandbox.parts.RouteEntry)); assert.ok(text(view.tree).includes('public SDK does not expose a verified authenticated-model inventory'));
    assert.equal(api.posts().length,0); view.dispose();
  }
});
await test('pricing cards keep unknown values and explicit zero distinct', async () => {
  const api=makeContext({'GET /pricing-cards':()=>({ok:true,cards:[]}), 'POST /pricing-card':call=>({ok:true,card:call.body.card})});
  const view=mount('PricingCard',{ctx:api.ctx,route:{provider:'new-cloud',model:'arbitrary/model'}});click(view,'▸ User-supplied pricing card');await view.flush();
  assert.equal(input(view,'Input USD per million tokens').props.value,''); assert.equal(input(view,'Output USD per million tokens').props.value,'');
  assert.equal(input(view,'Fixed fee').props.value,''); assert.equal(button(view,'Save user rate card').props.disabled,true);
  change(view,'Input USD per million tokens','0'); change(view,'Output USD per million tokens','2.5');
  change(view,'I confirm these are human-provided rates',true);click(view,'Save user rate card');await view.flush();
  const body=plain(api.posts()[0].body);assert.equal(body.confirmed,true);assert.equal(body.card.input,0);assert.equal(body.card.output,2.5);assert.equal(body.card.cache_read,null);assert.equal(body.card.request_cost,null);
  assert.equal('tier_threshold_tokens' in body.card,false);assert.equal(body.card.unit,'per_million_tokens');assert.equal(api.posts().length,1);view.dispose();
});
await test('pricing acknowledgement resets when a tier or rate changes', async () => {
  const api=makeContext({'GET /pricing-cards':()=>({ok:true,cards:[]})});const view=mount('PricingCard',{ctx:api.ctx,route:{provider:'provider',model:'model'}});
  click(view,'▸ User-supplied pricing card');await view.flush();change(view,'I confirm these are human-provided rates',true);
  change(view,'I know this rate card has no volume tiers',true);assert.equal(input(view,'I confirm these are human-provided rates').props.checked,false);
  change(view,'I confirm these are human-provided rates',true);change(view,'Fixed fee','0');assert.equal(input(view,'I confirm these are human-provided rates').props.checked,false);
  assert.equal(button(view,'Save user rate card').props.disabled,true);assert.equal(api.posts().length,0);view.dispose();
});
await test('saved unknown tiers are never presented as no tiers', async () => {
  const card={schema_version:1,provider:'provider',model:'model',unit:'per_token',currency:'USD',input:0.000001,output:0.000002,request_cost:0,tier_threshold_tokens:null,tiers_known:false};
  const api=makeContext({'GET /pricing-cards':()=>({ok:true,cards:[card]})});const view=mount('PricingCard',{ctx:api.ctx,route:null});click(view,'▸ User-supplied pricing card');await view.flush();
  change(view,'Load a saved rate card','0');assert.equal(input(view,'I know this rate card has no volume tiers').props.checked,false);
  assert.equal(input(view,'Input USD per token').props.value,0.000001);assert.equal(input(view,'Fixed fee').props.value,0);assert.equal(api.posts().length,0);view.dispose();
});
const unsupported={dispatch:{public_dispatch_method_present:true,guarded_dispatch_available:false,automatic_nested_tools:false,input_files_mode:'normal_host_tool_plan',save_to_mode:'normal_host_tool_plan',reason:'The current public dispatcher cannot verify the host guards.'}};
const modeItem={job_id:'job-a',routine_id:'routine-a',name:'An observed task',ready:true,no_ai_fit:true,mode:'tool',promotion_availability:{try_available:true,test_execution:'observe_normal_host_runs',automatic_replay_available:false,modes:{tool:{available:true,execution:'normal_host_tools'},prerun:{available:false,reason:'Guarded dispatch is unavailable.'},no_ai:{available:false,reason:'Guarded dispatch is unavailable.'}}}};
await test('unguarded dispatch cannot enable autonomous modes', async () => {
  const unsafe={...modeItem,promotion_availability:{...modeItem.promotion_availability,modes:{tool:{available:false},prerun:{available:true},no_ai:{available:true}}}};
  assert.equal(sandbox.parts.canUseMode(unsafe,'prerun',unsupported),false);assert.equal(sandbox.parts.canUseMode(unsafe,'no_ai',unsupported),false);assert.equal(sandbox.parts.promotionTarget(unsafe,unsupported),null);
  const calls=[];const view=mount('LiveOptions',{item:unsafe,compatibility:unsupported,act:(...args)=>calls.push(args)});await view.flush();
  assert.equal(button(view,'Run with no AI'),undefined);assert.equal(button(view,'Use verified pre-run'),undefined);assert.equal(calls.length,0);view.dispose();
});
await test('host-tool plans require an explicit mode choice', async () => {
  const calls=[];const view=mount('JobBubble',{item:modeItem,status:'testing',trialRuns:3,ctx:makeContext().ctx,compatibility:unsupported,act:(path,body)=>calls.push({path,body}),exp:{open:{'job-a':true},toggle:()=>{}}});await view.flush();
  const action=all(view).find(node=>node.type && node.type.name==='JobHeader').props.action;assert.equal(text(action),'Use host-tool plan');assert.equal(calls.length,0);action.props.onClick();view.render();
  assert.deepEqual(plain(calls),[{path:'/promote',body:{routine_id:'routine-a',mode:'tool'}}]);assert.ok(text(view.tree).includes('Automatic Promote unavailable'));view.dispose();
});
await test('unavailable replay never advertises stale projected savings', async () => {
  for (const status of ['testing','spotted']) {
    const view=mount('JobBubble',{item:{...modeItem,mode:null,saving_per_week:250,projection:{status:'unavailable'}},status,trialRuns:3,ctx:makeContext().ctx,compatibility:unsupported,act:()=>{},exp:{open:{'job-a':true},toggle:()=>{}}});await view.flush();
    const content=text(view.tree);assert.ok(content.includes('Needs measurement'));assert.ok(content.includes('No saving measured yet'));assert.ok(!content.includes('$250'));view.dispose();
  }
});
await test('native source preview requires review and resets approval on edits', async () => {
  const api=makeContext({'GET /native-cron':()=>({ok:true,state:{},config_fingerprint:'current'}),'POST /native-cron-preview':()=>({ok:true,can_apply:true,preview_sha256:'reviewed',source:{text:'print(42)',sha256:'source-hash'},blockers:[]})});
  const view=mount('NativeCronControl',{ctx:api.ctx,jobId:'job-a'});await view.flush();assert.equal(api.calls.length,0);click(view,'▸ Skip runs when nothing changed');await view.flush();
  change(view,'Python source under','snapshot.py');change(view,'This script only reads',true);click(view,'Preview source · no execution');await view.flush();assert.equal(button(view,'Attach reviewed source').props.disabled,true);
  change(view,'I approve attaching',true);change(view,'Python source under','different.py');assert.equal(button(view,'Attach reviewed source'),undefined);assert.equal(api.posts().filter(c=>c.base==='/native-cron').length,0);view.dispose();
});
await test('native source runtime updates require explicit source review', async () => {
  const api=makeContext({'GET /native-cron':()=>({ok:true,state:{status:'applied',mode:'monitor'},runtime_review_required:true,config_fingerprint:'current'})});
  const view=mount('NativeCronControl',{ctx:api.ctx,jobId:'job-a'});click(view,'▸ Skip runs when nothing changed');await view.flush();
  assert.ok(text(view.tree).includes('older or unverified runtime'));assert.equal(api.posts().length,0);assert.equal(button(view,'Undo native source').props.disabled,true);view.dispose();
});
await test('native source undo requires current configuration and explicit approval', async () => {
  const api=makeContext({'GET /native-cron':()=>({ok:true,state:{status:'applied',mode:'monitor'},config_fingerprint:'current'}),'POST /native-cron-undo':()=>({ok:true,state:{status:'undone'},message:'Restored.'})});
  const view=mount('NativeCronControl',{ctx:api.ctx,jobId:'job-a'});click(view,'▸ Skip runs when nothing changed');await view.flush();assert.equal(button(view,'Undo native source').props.disabled,true);change(view,'I approve restoring',true);click(view,'Undo native source');await view.flush();
  assert.deepEqual(plain(api.posts()[0].body),{job_id:'job-a',expected_config_fingerprint:'current',confirmed:true});view.dispose();
});
await test('observation starts only through its explicit control', async () => {
  const calls=[];const view=mount('JobBubble',{item:{...modeItem,ready:false},status:'spotted',trialRuns:3,ctx:makeContext().ctx,compatibility:unsupported,act:(path,body)=>calls.push({path,body}),exp:{open:{'job-a':true},toggle:()=>{}}});await view.flush();assert.equal(calls.length,0);const action=all(view).find(node=>node.type && node.type.name==='JobHeader').props.action;assert.equal(text(action),'Observe next runs');action.props.onClick();view.render();
  assert.deepEqual(plain(calls),[{path:'/try',body:{job_id:'job-a'}}]);assert.ok(text(view.tree).includes('normally executed Hermes runs'));view.dispose();
});
await test('compatibility explains normal host-tool file plans', async () => {
  const view=mount('CompatibilityCard',{state:{data:unsupported,error:null,load:()=>{}}});await view.flush();click(view,'▸ Capability details');
  const content=text(view.tree);assert.ok(content.includes('Automatic pre-run replay and zero-AI execution need'));assert.ok(content.includes('explicit host-tool read plan'));assert.ok(content.includes('explicit host-tool write plan'));
  view.dispose();
});

await test('unsupported unchanged policies expose only a run-always reset', async () => {
  for(const mode of ['tool','prerun']) for(const saved of ['repeat','quiet','auto']) {
    const calls=[];const view=mount('LiveOptions',{item:{...modeItem,mode,when_unchanged:saved},compatibility:unsupported,act:(path,body)=>calls.push({path,body})});await view.flush();
    assert.ok(text(view.tree).includes('is unavailable in this mode'));assert.equal(all(view).filter(node=>node.type==='button' && text(node).startsWith('If unchanged:')).length,0);
    assert.equal(calls.length,0);click(view,'Run always / clear saved skip');assert.deepEqual(plain(calls),[{path:'/skip',body:{routine_id:'routine-a',when:'run'}}]);view.dispose();
  }
});
await test('host-tool mode never offers automatic unchanged cycling', async () => {
  const guarded={dispatch:{guarded_dispatch_available:true,automatic_nested_tools:true}};
  const policy={...modeItem.promotion_availability,modes:{...modeItem.promotion_availability.modes,prerun:{available:true}}};
  const calls=[];const view=mount('LiveOptions',{item:{...modeItem,mode:'tool',when_unchanged:'quiet',promotion_availability:policy},compatibility:guarded,act:(path,body)=>calls.push({path,body})});await view.flush();
  assert.equal(all(view).filter(node=>node.type==='button' && text(node).startsWith('If unchanged:')).length,0);
  click(view,'Run always / clear saved skip');assert.deepEqual(plain(calls),[{path:'/skip',body:{routine_id:'routine-a',when:'run'}}]);view.dispose();
});
await test('chat projections identify current route rates and historical repricing', async () => {
  const state={session_id:'chat-a',enabled:true,can_start:true,this_chat:{tokens_removed:1000,once_at_main_price:.01,net_once:.01,cheap_cost:0},turns:[{turn_id:'old-turn',net_once:.01}],projection_pricing:{model:'main-a',provider:'cloud-a',input_usd_per_token:.00001,output_usd_per_token:.00002}};
  const view=mount('ThisChat',{state,onToggle:()=>{}});let content=text(view.tree);
  for(const expected of ['Current-price projection','main-a','cloud-a','changing the model or rates reprices','not historical billing','Current main prices per million tokens: $10','current-price one-read net']) assert.ok(content.includes(expected),expected);
  view.rerender({state:{...state,projection_pricing:{...state.projection_pricing,model:'main-b',input_usd_per_token:.001}},onToggle:()=>{}});content=text(view.tree);
  assert.ok(content.includes('main-b'));assert.equal(content.includes('main-a'),false);assert.ok(content.includes('$1000'));view.dispose();
});
await test('helper cache upper bounds remain distinct from unknown costs', async () => {
  const state={session_id:'chat-a',enabled:true,this_chat:{cheap_cost:.0022,upper_bound_cost_calls:1,unknown_cost_calls:0}};
  const view=mount('ThisChat',{state,onToggle:()=>{}});let content=text(view.tree);
  assert.ok(content.includes('Helper cost upper bound'));assert.ok(content.includes('highest applicable rate'));assert.ok(content.includes('missing or ambiguous cache details'));
  view.rerender({state:{...state,this_chat:{...state.this_chat,cheap_cost:null,unknown_cost_calls:1}},onToggle:()=>{}});content=text(view.tree);
  assert.equal(content.includes('Helper cost upper bound'),false);assert.ok(content.includes('Net savings are unknown.'));view.dispose();
});
await test('reasoning stays fixed and desktop imports remain public', async () => {
  assert.equal(source.includes('/reasoning-setting'),false); assert.equal(source.includes('Reasoning: also try lower'),false);
  assert.ok(source.includes('thinking less never shows as a saving'));
  const imports = [...source.matchAll(/^import .* from '([^']+)'/gm)].map(match => match[1]);
  assert.deepEqual(imports,['@hermes/plugin-sdk','react','react/jsx-runtime']);
  for (const forbidden of ['dangerouslySetInnerHTML','querySelector','MutationObserver','createElement(','innerHTML']) assert.equal(source.includes(forbidden),false,forbidden);
});
process.stdout.write(JSON.stringify(results));
})().catch(error => {console.error(error);process.exitCode=1;});
"""


@pytest.fixture(scope="module")
def ui_results():
    node = os.environ.get("CRYSTALLIZE_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the offline desktop behavior checks")
    completed = subprocess.run(
        [node, "-e", JS, str(ROOT / "desktop" / "plugin.js")],
        capture_output=True, text=True, timeout=25, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    results = json.loads(completed.stdout)
    assert [result["name"] for result in results] == CASES
    return {result["name"]: result for result in results}


@pytest.mark.parametrize("case", CASES)
def test_completion_controls(case, ui_results):
    result = ui_results[case]
    assert result["ok"], result.get("error", case)
