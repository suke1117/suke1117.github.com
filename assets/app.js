/* ==========================================================================
   app.js — 悩み → 筋トレメニュー生成エンジン
   ========================================================================== */

'use strict';

/* ---------------------------------------------------------------- utils */

// 再現性のある擬似乱数（「別パターンで再生成」のため seed を変える）
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
let SEED = Date.now() >>> 0;      // このシードが同じなら、同じ入力から同じプランが出る
let rand = mulberry32(SEED);

const $  = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

const GOAL_LABEL = {
  fatloss:'体脂肪を落とす', belly:'お腹まわり', core:'体幹', hypertrophy:'筋肥大',
  bulk:'体を大きく', strength:'筋力アップ', posture:'姿勢改善', stiffness:'肩こり解消',
  lowback:'腰にやさしく', knee:'膝にやさしく', shoulder:'肩にやさしく',
  endurance:'体力・スタミナ', glutes:'ヒップアップ', legs:'脚', flabbyarm:'二の腕',
  arms:'腕', chest:'胸', back:'背中', shoulders:'肩幅', stress:'ストレス解消',
  beginner:'運動習慣づくり', health:'健康・数値改善', looks:'見た目の印象',
};

const ENV_LABEL = { full:'フルジム', machine:'マシン中心', dumbbell:'ダンベル', body:'自重のみ' };
const WEEKDAYS  = { 1:['土'], 2:['火','土'], 3:['月','水','金'], 4:['月','火','木','金'], 5:['月','火','水','金','土'] };

/* ------------------------------------------------------- 1. 入力を読む */

function readInput() {
  const val = (name) => { const el = $(`input[name="${name}"]:checked`); return el ? el.value : null; };
  return {
    text:  $('#concern').value.trim(),
    chips: $$('input[name="preset"]:checked').map((c) => c.value),
    level: parseInt(val('level') || '1', 10),   // 1 初心者 / 2 中級 / 3 上級
    freq:  parseInt(val('freq')  || '3', 10),   // 週あたり回数
    mins:  parseInt(val('mins')  || '60', 10),  // 1回あたりの分数
    env:   val('env') || 'full',
    pains: $$('input[name="pain"]:checked').map((c) => c.value),
  };
}

/* ------------------------------------------------- 2. 悩みを解析する */

function analyze(input) {
  const score = {};      // goal -> 点数
  const matched = [];    // マッチしたルール
  const cautions = [];
  const flags = new Set(input.pains);   // 'lowback' | 'knee' | 'shoulder'

  const text = input.text.replace(/\s+/g, '');
  const addRule = (rule, weight) => {
    if (!matched.includes(rule)) matched.push(rule);
    rule.goals.forEach((g, i) => { score[g] = (score[g] || 0) + weight - i * 0.3; });
    if (rule.flag) flags.add(rule.flag);
    if (rule.caution && !cautions.includes(rule.caution)) cautions.push(rule.caution);
  };

  CONCERN_RULES.forEach((rule) => {
    if (input.chips.includes(rule.id)) addRule(rule, 4);
    if (text) {
      const hits = rule.kw.filter((k) => text.includes(k)).length;
      if (hits > 0) addRule(rule, Math.min(3 + hits * 0.6, 6));
    }
  });

  // 痛みチェックからも配慮タグを立てる
  if (flags.has('lowback')) { score.lowback = (score.lowback || 0) + 3; score.core = (score.core || 0) + 2; }
  if (flags.has('knee'))    { score.knee    = (score.knee    || 0) + 3; score.glutes = (score.glutes || 0) + 1; }
  if (flags.has('shoulder')){ score.shoulder= (score.shoulder|| 0) + 3; score.posture = (score.posture || 0) + 1; }

  // 何も読み取れなければ「全身バランス」プラン
  let fallback = false;
  if (matched.length === 0) {
    fallback = true;
    ['health', 'hypertrophy', 'fatloss', 'core'].forEach((g, i) => { score[g] = 4 - i * 0.5; });
  }

  // 初心者は必ず習慣づくりを加点
  if (input.level === 1) score.beginner = (score.beginner || 0) + 2;

  const ranked = Object.keys(score).sort((a, b) => score[b] - score[a]);
  return { score, ranked, matched, cautions, flags, fallback };
}

/* --------------------------------------------- 3. セット・レップの方針 */

function pickScheme(input, an) {
  const has = (g, min) => (an.score[g] || 0) >= (min || 2.5);
  const safety = an.flags.size > 0;

  const sc = (g) => an.score[g] || 0;
  if (has('strength', 3) && input.level >= 2 &&
      sc('strength') >= Math.max(sc('hypertrophy'), sc('bulk'), sc('fatloss')) - 0.5) {
    return { key:'strength', label:'筋力重視', sets:5, reps:'5回', rest:150,
      cue:'5回でギリギリ上がる重さ（余力1回）',
      desc:'高重量・低回数・長い休憩。神経系を鍛えて挙上重量を伸ばします。' };
  }
  if (safety || has('lowback', 3) || has('stiffness', 3) || (has('posture', 3) && input.level === 1)) {
    return { key:'safe', label:'コンディション重視', sets:3, reps:'12〜15回', rest:60,
      cue:'軽めで、効いている場所を感じられる重さ',
      desc:'重量よりフォームと可動域。痛みの出ない範囲で、弱くなった筋肉を起こしていきます。' };
  }
  if (has('fatloss', 3) || has('endurance', 3) || has('belly', 3)) {
    return { key:'fatloss', label:'脂肪燃焼・引き締め', sets:3, reps:'12〜15回', rest:45,
      cue:'15回目がきつい重さ',
      desc:'休憩を短くして心拍を保ち、消費カロリーと筋持久力を同時に稼ぎます。' };
  }
  if (has('hypertrophy', 3) || has('bulk', 3) || input.level >= 2) {
    return { key:'hyper', label:'筋肥大重視', sets:input.level >= 2 ? 4 : 3, reps:'8〜12回', rest:90,
      cue:'10回目でフォームが崩れかける重さ（余力1〜2回）',
      desc:'中重量で総ボリュームを稼ぐ、筋肉を大きくする王道の組み方です。' };
  }
  return { key:'beginner', label:'フォーム習得', sets:3, reps:'10〜12回', rest:60,
    cue:'余力を2回残して終われる重さ',
    desc:'まずは動作を体に覚えさせる時期。重量は毎週少しずつで十分です。' };
}

/* 種目ごとの負荷（セット数・回数・休憩）を決める */
const ISOLATION = ['arms_bi', 'arms_tri', 'shoulder_lat', 'rear_delt', 'accessory', 'core'];

function prescription(ex, scheme) {
  if (ex.dose) {                                   // 負荷が固定されている種目
    const m = /(\d+)\s*セット/.exec(ex.dose);
    return { fixed:ex.dose, sets:m ? parseInt(m[1], 10) : 1, reps:'', rest:0 };
  }
  let sets = scheme.sets, reps = scheme.reps, rest = scheme.rest;
  if (ISOLATION.includes(ex.pattern)) {             // 単関節種目は少し軽く、休憩も短く
    sets = Math.max(2, Math.min(scheme.sets, 3));
    if (scheme.key === 'strength') { reps = '8〜10回'; rest = 75; }
    else if (scheme.key === 'hyper') { reps = '12〜15回'; rest = 60; }
    else rest = Math.min(rest, 60);
  }
  return { fixed:'', sets, reps, rest };
}

function doseFor(ex, scheme) {
  const p = prescription(ex, scheme);
  return p.fixed || `${p.sets}セット × ${p.reps}（休憩${p.rest}秒）`;
}

function setsFor(ex, scheme) { return prescription(ex, scheme).sets; }

/* おおよその所要時間（分） */
function timeFor(ex, scheme) {
  if (ex.dose) {
    const m = /(\d+)\s*分/.exec(ex.dose);
    if (m) return parseInt(m[1], 10);
    return ['mobility', 'stretch', 'warmup'].includes(ex.pattern) ? 2 : 3;
  }
  const p = prescription(ex, scheme);
  return Math.round((p.sets * (40 + p.rest)) / 60);
}

/* ------------------------------------------------------- 4. 種目を選ぶ */

function candidates(pattern, input, an) {
  const maxLevel = input.level;
  const list = EX_DB.filter((ex) => {
    if (ex.pattern !== pattern) return false;
    if (!ex.equip.includes(input.env)) return false;
    if (ex.avoid && ex.avoid.some((a) => an.flags.has(a))) return false;
    return true;
  });
  const fit = list.filter((ex) => ex.level <= maxLevel);
  const pool = fit.length ? fit : list.filter((ex) => ex.level <= maxLevel + 1);
  return (pool.length ? pool : list).slice();
}

function scoreEx(ex, an, usage) {
  let s = 0;
  (ex.goals || []).forEach((g) => { s += (an.score[g] || 0); });
  s -= (usage[ex.id] || 0) * 9;        // 週の中で同じ種目が続かないように
  s += rand() * 1.5;
  if (ex.level === 1) s += 0.4;
  return s;
}

function pick(pattern, input, an, usage, usedToday) {
  const pool = candidates(pattern, input, an).filter((ex) => !usedToday.has(ex.id));
  if (!pool.length) return null;
  pool.sort((a, b) => scoreEx(b, an, usage) - scoreEx(a, an, usage));
  const ex = pool[0];
  usage[ex.id] = (usage[ex.id] || 0) + 1;
  usedToday.add(ex.id);
  return { ex, alts: pool };
}

/* --------------------------------------------------- 5. 分割を決める */

function splitPlan(input, an) {
  const f = input.freq;
  const beginnerish = input.level === 1 || (an.score.beginner || 0) >= 3;

  if (f === 1) return { name:'全身1日プラン', days:[
    { title:'全身', patterns:['squat','push_h','pull_v','hinge','core'] } ] };

  if (f === 2) return { name:'全身2分割', days:[
    { title:'全身A（押す寄り）', patterns:['squat','push_h','pull_h','core'] },
    { title:'全身B（引く寄り）', patterns:['hinge','pull_v','push_v','core'] } ] };

  if (f === 3) {
    if (beginnerish) return { name:'全身トレ×3', days:[
      { title:'全身A', patterns:['squat','push_h','pull_v','core'] },
      { title:'全身B', patterns:['hinge','push_v','pull_h','core'] },
      { title:'全身C', patterns:['lunge','push_h','pull_h','core'] } ] };
    return { name:'プッシュ／プル／脚の3分割', days:[
      { title:'プッシュ（胸・肩・三頭）', patterns:['push_h','push_v','shoulder_lat','arms_tri'] },
      { title:'プル（背中・二頭）',       patterns:['pull_v','pull_h','rear_delt','arms_bi'] },
      { title:'脚・体幹',                 patterns:['squat','hinge','accessory','core'] } ] };
  }

  if (f === 4) return { name:'上半身／下半身の4分割', days:[
    { title:'上半身A（押す）', patterns:['push_h','pull_v','shoulder_lat','arms_tri'] },
    { title:'下半身A（膝中心）', patterns:['squat','hinge','accessory','core'] },
    { title:'上半身B（引く）', patterns:['pull_h','push_v','rear_delt','arms_bi'] },
    { title:'下半身B（股関節中心）', patterns:['hinge','lunge','accessory','core'] } ] };

  return { name:'プッシュ／プル／脚＋上下の5分割', days:[
    { title:'プッシュ（胸・肩）',   patterns:['push_h','push_v','shoulder_lat','arms_tri'] },
    { title:'プル（背中・二頭）',   patterns:['pull_v','pull_h','rear_delt','arms_bi'] },
    { title:'脚（膝中心）',         patterns:['squat','lunge','accessory','core'] },
    { title:'上半身（弱点補強）',   patterns:['push_h','pull_h','shoulder_lat','arms_bi'] },
    { title:'下半身・体幹',         patterns:['hinge','accessory','core','core'] } ] };
}

/* 悩みに応じて追加したい種目枠 */
function bonusPatterns(an) {
  const out = [];
  const s = (g) => an.score[g] || 0;
  const add = (p, v) => { if (v >= 3) out.push({ p, v }); };
  add('core',        Math.max(s('belly'), s('core')));
  add('rear_delt',   Math.max(s('posture'), s('stiffness')));
  add('shoulder_lat',Math.max(s('shoulders'), s('looks')));
  add('arms_tri',    Math.max(s('flabbyarm'), s('arms')));
  add('arms_bi',     s('arms'));
  add('hinge',       s('glutes'));
  add('accessory',   Math.max(s('glutes'), s('knee'), s('lowback')));
  add('lunge',       s('legs'));
  out.sort((a, b) => b.v - a.v);
  return out.map((o) => o.p);
}

/* ------------------------------------------------- 6. メニューを組む */

function buildProgram(input, an) {
  const scheme = pickScheme(input, an);
  const split = splitPlan(input, an);
  const usage = {};
  const weekBonus = {};
  const wantCardio = ['fatloss','endurance','health','stress','belly']
    .some((g) => (an.score[g] || 0) >= 3);
  const bonus = bonusPatterns(an);

  const days = split.days.map((tpl, di) => {
    const usedToday = new Set();
    const blocks = [];
    let budget = input.mins;

    /* --- ウォームアップ --- */
    const warm = [];
    const lowerDay = tpl.patterns.some((p) => ['squat','hinge','lunge'].includes(p));
    if (!lowerDay) usedToday.add('wu_squat');   // 上半身の日にスクワットのW-UPは出さない
    const w1 = pick('warmup', input, an, usage, usedToday);
    if (w1) warm.push(w1);
    const needMobility = ['posture','stiffness','lowback','knee','shoulder']
      .some((g) => (an.score[g] || 0) >= 3);
    if (needMobility) {
      const m = pick('mobility', input, an, usage, usedToday);
      if (m) warm.push(m);
    }
    const wtime = warm.reduce((t, w) => t + timeFor(w.ex, scheme), 0);
    budget -= Math.max(wtime, 5);
    blocks.push({ title:'ウォームアップ', items:warm, note:'ここを飛ばすとケガの確率が跳ね上がります。' });

    /* --- クールダウン枠を先に確保 --- */
    budget -= 5;

    /* --- 有酸素枠 --- */
    let cardioPick = null;
    if (wantCardio && input.mins >= 45 && (di % 2 === 0 || input.freq <= 2)) {
      cardioPick = pick('cardio', input, an, usage, usedToday);
      if (cardioPick) budget -= Math.min(timeFor(cardioPick.ex, scheme), 20);
    }

    /* --- メイン種目 --- */
    const main = [];
    const maxItems = Math.min(10, Math.max(3, Math.round(input.mins / 8)));
    const tryAdd = (p) => {
      if (main.length >= maxItems) return false;
      const got = pick(p, input, an, usage, usedToday);
      if (!got) return false;
      budget -= timeFor(got.ex, scheme);
      main.push(got);
      return true;
    };

    // (1) その日の基本パターンを埋める
    for (const p of tpl.patterns) {
      if (budget <= 0 && main.length >= 3) break;
      tryAdd(p);
    }
    // (2) 悩みに応じた追加枠。1つのパターンは週2回までに制限する
    for (const p of bonus) {
      if (budget < 5 || main.length >= maxItems - 1) break;
      if ((weekBonus[p] || 0) >= 2) continue;
      if (tryAdd(p)) weekBonus[p] = (weekBonus[p] || 0) + 1;
    }
    // (3) まだ時間が余るなら、その日の部位からもう1種目ずつ足していく
    const fillers = tpl.patterns.concat(['core', 'accessory']);
    for (let i = 0, guard = 0; budget >= 6 && main.length < maxItems && guard < 24; i++, guard++) {
      tryAdd(fillers[i % fillers.length]);
    }
    // (4) 時間が厳しくても最低3種目は確保する
    for (let i = 0; main.length < 3 && i < fillers.length * 2; i++) tryAdd(fillers[i % fillers.length]);
    const mainBlock = { title:'メイン', items:main, ordered:true,
      note:`負荷の目安：${scheme.cue}。最後の1〜2回がきついと感じる重さを選びます。` };
    blocks.push(mainBlock);

    if (cardioPick) {
      blocks.push({ title:'仕上げの有酸素', items:[cardioPick],
        note:'筋トレのあとに行うと脂肪が使われやすい状態になっています。' });
    }

    /* --- クールダウン --- */
    const cool = [];
    const c1 = pick('stretch', input, an, usage, usedToday);
    if (c1) cool.push(c1);
    const c2 = pick('stretch', input, an, usage, usedToday);
    if (c2) cool.push(c2);
    blocks.push({ title:'クールダウン', items:cool, note:'呼吸を止めずに、痛気持ちいい手前で止めます。' });

    const est = Math.max(
      blocks.reduce((t, b) => t + b.items.reduce((s, i) => s + timeFor(i.ex, scheme), 0), 0), 20);
    if (est > input.mins + 5) {
      mainBlock.note += ` 時間が足りない日は、下の種目から各1セットずつ減らして調整してください。`;
    }
    return { title:tpl.title, day:(WEEKDAYS[input.freq] || [])[di] || '', blocks, est };
  });

  return { scheme, splitName:split.name, days, input, an, wantCardio };
}

/* ------------------------------------------------------- 7. 文章まわり */

function progressionNotes(program) {
  const { scheme, input, an } = program;
  const notes = [
    `<strong>重さの伸ばし方：</strong>決めた回数を全セットきれいにこなせたら、次回は2.5〜5kg（自重なら回数を2回）足します。これが筋肉が成長し続ける唯一の条件です。`,
    `<strong>記録をつける：</strong>種目・重量・回数をスマホにメモしてください。前回の数字が分かるだけで伸びが変わります。`,
    `<strong>効果が見えるまで：</strong>体感は2〜3週間、見た目の変化は6〜8週間が目安です。最初の2週間は「行けたかどうか」だけを評価してください。`,
  ];
  if (scheme.key === 'fatloss' || (an.score.fatloss || 0) >= 3) {
    notes.push(`<strong>食事：</strong>トレーニングだけで落ちる脂肪はわずかです。消費が摂取を上回らないと減りません。まずは間食と飲み物の見直しから。`);
    notes.push(`<strong>有酸素：</strong>週合計150分の早歩き（1日20〜25分）が目安。筋トレのあと、または別の日に。`);
  }
  if ((an.score.hypertrophy || 0) >= 3 || (an.score.bulk || 0) >= 3) {
    notes.push(`<strong>タンパク質：</strong>体重1kgあたり1.6g以上を目標に。体重60kgなら約100g、3食＋補食に分けて摂ります。`);
    notes.push(`<strong>睡眠：</strong>筋肉が作られるのは寝ている間です。7時間を切る日が続くと伸びが止まります。`);
  }
  if ((an.score.posture || 0) >= 3 || (an.score.stiffness || 0) >= 3) {
    notes.push(`<strong>日常での意識：</strong>ジムの1時間より、デスクでの8時間のほうが姿勢に効きます。1時間に1回立ち上がり、肩を後ろに回してください。`);
  }
  if ((an.score.beginner || 0) >= 3 || input.level === 1) {
    notes.push(`<strong>最初の4週間：</strong>重量を追わずフォーム最優先。マシンの使い方はジムスタッフに遠慮なく聞いてください。`);
  }
  if ((an.score.stress || 0) >= 3) {
    notes.push(`<strong>気分が乗らない日：</strong>「ウォームアップだけやって帰る」でOKにしておくと習慣が途切れません。だいたいそのまま続きます。`);
  }
  return notes;
}

/* ==========================================================================
   8. 描画
   ========================================================================== */

let CURRENT = null;     // いま表示しているプラン
let PLAN_KEY = '';      // チェック状態の保存キー
let DONE = {};          // { dayIndex: [種目id, ...] }
let activeDay = 0;
let showAll = false;

const ICON = {
  copy:'<svg viewBox="0 0 24 24"><rect x="8.5" y="8.5" width="12" height="12" rx="2.4"/><path d="M15.5 5.5H5.8a2 2 0 0 0-2 2v9.7"/></svg>',
  print:'<svg viewBox="0 0 24 24"><path d="M7 9.5V3.5h10v6M7 18.5H5a1.6 1.6 0 0 1-1.6-1.6v-4.8A1.6 1.6 0 0 1 5 10.5h14a1.6 1.6 0 0 1 1.6 1.6v4.8a1.6 1.6 0 0 1-1.6 1.6h-2"/><rect x="7" y="14.5" width="10" height="6" rx="1.2"/></svg>',
  shuffle:'<svg viewBox="0 0 24 24"><path d="M3.5 6.5h3.8l9.4 11h3.8M3.5 17.5h3.8l3.1-3.6M17.2 6.5h3.5M16.8 3.8l3.7 2.7-3.7 2.7M16.8 14.8l3.7 2.7-3.7 2.7"/></svg>',
  edit:'<svg viewBox="0 0 24 24"><path d="M4 20h4.2l9.6-9.6a2.1 2.1 0 0 0 0-3l-1.2-1.2a2.1 2.1 0 0 0-3 0L4 15.8Z"/></svg>',
  clock:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.6"/><path d="M12 7.4V12l3 1.8"/></svg>',
  swap:'<svg viewBox="0 0 24 24"><path d="M4.5 8.5h13M14.4 5.4l3.1 3.1-3.1 3.1M19.5 15.5h-13M9.6 12.4l-3.1 3.1 3.1 3.1"/></svg>',
  check:'<svg viewBox="0 0 24 24"><path d="M5 12.6 9.7 17 19 7.6"/></svg>',
  alert:'<svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2"><path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4M12 16.6v.2"/></svg>',
};

/* ------------------------------------------------ 進捗（チェック）の保存 */

function planKey(p) {
  const src = p.splitName + '|' + p.days.map((d) =>
    d.title + ':' + d.blocks.map((b) => b.items.map((i) => i.ex.id).join(',')).join(';')).join('|');
  let h = 5381;
  for (let i = 0; i < src.length; i++) h = ((h * 33) ^ src.charCodeAt(i)) >>> 0;
  return 'plan-' + h.toString(36);
}
function loadProgress(key) {
  try {
    const raw = JSON.parse(localStorage.getItem('gym-menu-progress') || 'null');
    return raw && raw.key === key ? raw.done : {};
  } catch (e) { return {}; }
}
function saveProgress() {
  try { localStorage.setItem('gym-menu-progress', JSON.stringify({ key:PLAN_KEY, done:DONE })); } catch (e) {}
}
const isDone = (d, id) => (DONE[d] || []).includes(id);

/* ------------------------------------------------------------ 部品 */

function dayItems(d) {
  return CURRENT.days[d].blocks.reduce((a, b) => a.concat(b.items), []);
}
function dayCount(d) {
  const all = dayItems(d);
  return { done: all.filter((it) => isDone(d, it.ex.id)).length, total: all.length };
}

function itemHTML(item, scheme, d, b, i, num) {
  const ex = item.ex;
  const p = prescription(ex, scheme);
  const why = (ex.goals || [])
    .filter((g) => (CURRENT.an.score[g] || 0) >= 2.5 && GOAL_LABEL[g])
    .slice(0, 3).map((g) => GOAL_LABEL[g]).join('・');
  const done = isDone(d, ex.id);
  const dose = p.fixed
    ? `<span>${p.fixed}</span>`
    : `<span>${p.sets}セット × ${p.reps}</span><span class="rest">休憩 ${p.rest}秒</span>`;

  return `
    <li class="ex${done ? ' done' : ''}" data-d="${d}" data-b="${b}" data-i="${i}">
      <button class="check" type="button" data-act="check" aria-pressed="${done}"
              aria-label="${ex.name} を完了にする">${ICON.check}</button>
      <div class="ex-body">
        <div class="ex-name">${num ? `<span class="idx">${num}</span>` : ''}${ex.name}</div>
        <div class="ex-dose">${dose}</div>
        ${ex.note ? `<p class="ex-note">${ex.note}</p>` : ''}
        ${why ? `<div class="ex-why"><b>狙い</b> ${why}</div>` : ''}
      </div>
      <div class="ex-actions">
        ${p.rest ? `<button class="ic" type="button" data-act="timer" data-sec="${p.rest}">${ICON.clock}${p.rest}秒</button>` : ''}
        ${item.alts && item.alts.length > 1 ? `<button class="ic" type="button" data-act="swap">${ICON.swap}差し替え</button>` : ''}
      </div>
    </li>`;
}

function blockHTML(block, scheme, d, b) {
  let n = 0;
  return `
    <div class="block">
      <div class="block-head"><h4>${block.title}</h4><span class="rule"></span></div>
      <ul class="ex-list">
        ${block.items.map((it, i) => itemHTML(it, scheme, d, b, i, block.ordered ? ++n : 0)).join('')}
      </ul>
      ${block.note ? `<p class="block-note">${block.note}</p>` : ''}
    </div>`;
}

function dayPanelHTML(day, d) {
  const c = dayCount(d);
  return `
    <section class="card day-panel" id="day-${d}" role="tabpanel" aria-labelledby="tab-${d}" ${d === 0 ? '' : 'hidden'}>
      <div class="day-head">
        <div>
          <h3>${day.title}</h3>
          <div class="day-sub">DAY ${d + 1}${day.day ? ` ・ ${day.day}曜が目安` : ''}</div>
        </div>
        <span class="meta">所要 約${day.est}分<br>${dayItems(d).length}種目</span>
      </div>
      <div class="progress" data-prog="${d}">
        <div class="bar"><i style="width:${c.total ? (c.done / c.total) * 100 : 0}%"></i></div>
        <div class="txt">
          <span class="cnt">${c.done} / ${c.total} 完了</span>
          <button type="button" data-act="reset" data-d="${d}">チェックをリセット</button>
        </div>
      </div>
      ${day.blocks.filter((b) => b.items.length).map((b, bi) => blockHTML(b, CURRENT.scheme, d, bi)).join('')}
    </section>`;
}

/* --------------------------------------------- 週の部位別セット数グラフ */

function volumeData(program) {
  const map = {};
  program.days.forEach((day, di) => day.blocks.forEach((b) => {
    if (b.title !== 'メイン') return;
    b.items.forEach((it) => {
      const mg = MUSCLE_BY_ID[it.ex.id] || MUSCLE_BY_PATTERN[it.ex.pattern];
      if (!mg || mg === '有酸素') return;
      if (!map[mg]) map[mg] = { sets:0, days:[] };
      map[mg].sets += setsFor(it.ex, program.scheme);
      if (!map[mg].days.includes(di + 1)) map[mg].days.push(di + 1);
    });
  }));
  // 鍛えていない部位も0として出す（抜けが見えることに意味がある）
  return MUSCLE_ORDER.filter((m) => m !== '有酸素')
    .map((name) => ({ name, sets:(map[name] || { sets:0 }).sets, days:(map[name] || { days:[] }).days }))
    .sort((a, b) => b.sets - a.sets || MUSCLE_ORDER.indexOf(a.name) - MUSCLE_ORDER.indexOf(b.name));
}

function chartHTML(program) {
  const rows = volumeData(program);
  if (!rows.length) return '';
  const max = Math.max(1, ...rows.map((r) => r.sets));
  const bars = rows.map((r, i) => `
    <div class="bar-row${r.sets ? '' : ' zero'}"
         title="${r.sets ? `${r.name}：週${r.sets}セット（DAY ${r.days.join('・')}）`
                         : `${r.name}：このプランには入っていません`}">
      <span class="bar-label">${r.name}</span>
      <div class="bar-track">
        ${r.sets ? `<div class="bar-fill" style="width:${Math.max((r.sets / max) * 100, 3)}%;animation-delay:${i * 45}ms"></div>` : ''}
      </div>
      <span class="bar-val">${r.sets}<span>セット</span></span>
    </div>`).join('');

  return `
    <section class="card">
      <div class="card-head" style="margin-bottom:14px">
        <div>
          <span class="eyebrow">Weekly volume</span>
          <h2 style="margin-top:4px">週の部位別セット数</h2>
          <p class="hint">メイン種目の合計。悩みに直結する部位に厚みが出ているか確認できます。</p>
        </div>
      </div>
      <div class="chart">${bars}</div>
      <p class="chart-foot">筋肉を増やす目的なら、1部位あたり週10〜20セットが目安です。少ない部位は「差し替え」や回数の追加で足せます。</p>
    </section>`;
}

/* ------------------------------------------------------------ 全体描画 */

function render(program, quiet) {
  CURRENT = program;
  PLAN_KEY = planKey(program);
  DONE = loadProgress(PLAN_KEY);
  activeDay = 0; showAll = false;

  const { scheme, splitName, days, input, an } = program;
  const topGoals = an.ranked.filter((g) => GOAL_LABEL[g] && an.score[g] >= 2.5).slice(0, 6);

  const toolbar = `
    <div class="toolbar">
      <button class="btn btn-ghost" data-act="copy">${ICON.copy}テキストでコピー</button>
      <button class="btn btn-ghost" data-act="print">${ICON.print}印刷 / PDF</button>
      <button class="btn btn-ghost" data-act="again">${ICON.shuffle}別パターンで再生成</button>
      <button class="btn btn-ghost" data-act="edit">${ICON.edit}条件を変える</button>
    </div>`;

  const plan = `
    <section class="plan">
      <span class="eyebrow">Your plan</span>
      <h2>${splitName}</h2>
      <p class="plan-meta">週${input.freq}回 ・ 1回${input.mins}分 ・ ${ENV_LABEL[input.env]}</p>
      <div class="tags">
        ${topGoals.map((g) => `<span class="tag brand">${GOAL_LABEL[g]}</span>`).join('')}
        ${Array.from(an.flags).map((f) => `<span class="tag line">${GOAL_LABEL[f] || f} に配慮</span>`).join('')}
      </div>
      <dl class="stats">
        <div class="stat"><dt>組み方</dt><dd>${scheme.label}</dd></div>
        <div class="stat"><dt>基本セット</dt><dd>${scheme.sets}セット × ${scheme.reps}</dd></div>
        <div class="stat"><dt>セット間の休憩</dt><dd>${scheme.rest}秒</dd></div>
      </dl>
      <p class="scheme-desc">${scheme.desc}</p>
    </section>`;

  const reading = `
    <section class="card">
      <div class="card-head" style="margin-bottom:14px">
        <div><h2>悩みから読み取ったこと</h2></div>
      </div>
      <ul class="read-list">
        ${an.matched.length
          ? an.matched.slice(0, 5).map((r) => `<li><span class="k">${r.label}</span><span>${r.msg}</span></li>`).join('')
          : `<li><span class="k">標準プラン</span><span>具体的な悩みが読み取れなかったため、全身をバランスよく鍛える内容にしました。気になる部位を書き足すと、そこに寄せたメニューになります。</span></li>`}
      </ul>
    </section>`;

  const nav = `
    <div class="daynav" role="tablist" aria-label="トレーニング日">
      ${days.map((d, i) => {
        const c = dayCount(i);
        return `<button role="tab" id="tab-${i}" type="button" data-act="day" data-d="${i}"
                 aria-controls="day-${i}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}">
                 DAY ${i + 1}<span class="badge">${c.done}/${c.total}</span></button>`;
      }).join('')}
      <button type="button" data-act="all" class="all-toggle">全日まとめて表示</button>
    </div>`;

  const notes = `
    <section class="card">
      <div class="card-head" style="margin-bottom:14px"><div><h2>続けるためのポイント</h2></div></div>
      <ul class="note-list">${progressionNotes(program).map((n) => `<li>${n}</li>`).join('')}</ul>
      <div class="callout">
        <strong>${ICON.alert}安全のために</strong>
        ${an.cautions.map((c) => `<div>・${c}</div>`).join('')}
        <div>・痛みが出る動作は中止してください。「効いている感覚」と「関節の痛み」は別物です。</div>
        <div>・このプランは一般的な運動指導の範囲の情報です。持病・服薬・妊娠中・リハビリ中の方は医師の指示を優先してください。</div>
      </div>
    </section>`;

  const el = $('#result');
  el.innerHTML = toolbar + plan + reading + chartHTML(program) + nav +
    days.map((d, i) => dayPanelHTML(d, i)).join('') + notes;
  el.hidden = false;

  $$('#result > *').forEach((node, i) => {
    node.classList.add('reveal');
    node.style.animationDelay = Math.min(i * 55, 330) + 'ms';
  });

  if (!quiet) {
    requestAnimationFrame(() => {
      const y = el.getBoundingClientRect().top + window.scrollY - 60;
      window.scrollTo({ top:y, behavior:'smooth' });
    });
  }
}

/* ------------------------------------------------------------ 操作 */

function setDay(d) {
  activeDay = d;
  showAll = false;
  $$('#result .day-panel').forEach((p, i) => { p.hidden = i !== d; });
  $$('#result [data-act="day"]').forEach((b, i) => {
    b.setAttribute('aria-selected', String(i === d));
    b.tabIndex = i === d ? 0 : -1;
  });
  const t = $('.all-toggle');
  if (t) t.classList.remove('on');
  const nav = $('.daynav');
  const panel = $('#day-' + d);
  if (panel && nav) {
    const y = panel.getBoundingClientRect().top + window.scrollY - nav.offsetHeight - 62;
    if (window.scrollY > y) window.scrollTo({ top:y, behavior:'smooth' });
  }
}

function bindDayKeys() {
  $('#result').addEventListener('keydown', (e) => {
    if (!e.target.matches('[data-act="day"]')) return;
    const tabs = $$('#result [data-act="day"]');
    const at = tabs.indexOf(e.target);
    let to = -1;
    if (e.key === 'ArrowRight') to = (at + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') to = (at - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = tabs.length - 1;
    if (to < 0) return;
    e.preventDefault();
    tabs[to].focus();
    setDay(to);
  });
}

function toggleAll() {
  showAll = !showAll;
  $$('#result .day-panel').forEach((p, i) => { p.hidden = showAll ? false : i !== activeDay; });
  const t = $('.all-toggle');
  t.textContent = showAll ? '1日ずつ表示' : '全日まとめて表示';
  t.classList.toggle('on', showAll);
}

function refreshDay(d) {
  const c = dayCount(d);
  const prog = $(`[data-prog="${d}"]`);
  if (prog) {
    $('.bar i', prog).style.width = (c.total ? (c.done / c.total) * 100 : 0) + '%';
    $('.cnt', prog).textContent = `${c.done} / ${c.total} 完了`;
  }
  const tab = $(`[data-act="day"][data-d="${d}"]`);
  if (tab) {
    $('.badge', tab).textContent = `${c.done}/${c.total}`;
    tab.classList.toggle('done', c.done === c.total && c.total > 0);
  }
}

function toggleCheck(li) {
  const d = +li.dataset.d;
  const id = CURRENT.days[d].blocks[+li.dataset.b].items[+li.dataset.i].ex.id;
  const list = DONE[d] || (DONE[d] = []);
  const at = list.indexOf(id);
  if (at >= 0) list.splice(at, 1); else list.push(id);
  const on = at < 0;
  li.classList.toggle('done', on);
  $('.check', li).setAttribute('aria-pressed', String(on));
  saveProgress();
  refreshDay(d);
  const c = dayCount(d);
  if (on && c.done === c.total) toast('DAY ' + (d + 1) + ' 完了。おつかれさまでした');
}

function handleSwap(li) {
  const d = +li.dataset.d, b = +li.dataset.b, i = +li.dataset.i;
  const item = CURRENT.days[d].blocks[b].items[i];
  const idx = item.alts.findIndex((e) => e.id === item.ex.id);
  const used = new Set(dayItems(d).map((it) => it.ex.id));
  const next = item.alts.find((e, k) => k > idx && !used.has(e.id))
    || item.alts.find((e) => e.id !== item.ex.id && !used.has(e.id));
  if (!next) { toast('ほかに条件に合う種目がありません'); return; }

  const wasDone = isDone(d, item.ex.id);
  if (wasDone) toggleCheckState(d, item.ex.id, false);
  item.ex = next;
  const num = li.querySelector('.idx') ? li.querySelector('.idx').textContent : 0;
  li.outerHTML = itemHTML(item, CURRENT.scheme, d, b, i, num);
  PLAN_KEY = planKey(CURRENT);
  saveProgress();
  savePlan(readInput());
  refreshDay(d);
  toast('「' + next.name + '」に差し替えました');
}
function toggleCheckState(d, id, on) {
  const list = DONE[d] || (DONE[d] = []);
  const at = list.indexOf(id);
  if (on && at < 0) list.push(id);
  if (!on && at >= 0) list.splice(at, 1);
}

/* ------------------------------------------------------------ タイマー */

const RestTimer = {
  raf:null, endAt:0, total:0, name:'',
  el:null, num:null, arc:null, C: 2 * Math.PI * 16.2,

  init() {
    this.el = $('#timer'); this.num = $('#timer-num'); this.arc = $('#timer-arc');
    this.arc.style.strokeDasharray = this.C;
    $('#timer-stop').addEventListener('click', () => this.stop());
    $('#timer-add').addEventListener('click', () => { this.endAt += 15000; this.tick(); });
  },
  start(sec, name) {
    this.total = sec * 1000; this.endAt = Date.now() + this.total; this.name = name;
    $('#timer-name').textContent = '休憩 ' + sec + '秒';
    $('#timer-next').textContent = name;
    this.el.classList.add('show');
    cancelAnimationFrame(this.raf);
    this.loop();
  },
  loop() {
    this.tick();
    if (Date.now() < this.endAt) this.raf = requestAnimationFrame(() => this.loop());
    else this.finish();
  },
  tick() {
    const left = Math.max(0, this.endAt - Date.now());
    this.num.textContent = Math.ceil(left / 1000);
    const ratio = this.total ? left / this.total : 0;
    this.arc.style.strokeDashoffset = this.C * (1 - Math.min(ratio, 1));
  },
  finish() {
    this.num.textContent = '0';
    this.arc.style.strokeDashoffset = this.C;
    if (navigator.vibrate) { try { navigator.vibrate([120, 70, 120]); } catch (e) {} }
    this.beep();
    toast('休憩終了。次のセットへ');
    setTimeout(() => this.el.classList.remove('show'), 1400);
  },
  stop() { cancelAnimationFrame(this.raf); this.el.classList.remove('show'); },
  beep() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      [0, 0.18].forEach((t) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sine'; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.22, ctx.currentTime + t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.13);
        o.connect(g); g.connect(ctx.destination);
        o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.15);
      });
      setTimeout(() => ctx.close(), 800);
    } catch (e) {}
  },
};

/* ------------------------------------------------------- 結果のイベント */

function bindResult() {
  $('#result').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    const li = btn.closest('.ex');

    if (act === 'check') toggleCheck(li);
    else if (act === 'swap') handleSwap(li);
    else if (act === 'timer') {
      const name = $('.ex-name', li).textContent.replace(/^\d+/, '').trim();
      RestTimer.start(+btn.dataset.sec, name);
    }
    else if (act === 'day') setDay(+btn.dataset.d);
    else if (act === 'all') toggleAll();
    else if (act === 'reset') {
      const d = +btn.dataset.d;
      DONE[d] = []; saveProgress();
      $$(`#day-${d} .ex`).forEach((row) => {
        row.classList.remove('done');
        $('.check', row).setAttribute('aria-pressed', 'false');
      });
      refreshDay(d);
      toast('チェックをリセットしました');
    }
    else if (act === 'print') { const w = showAll; if (!w) toggleAll(); setTimeout(() => window.print(), 60); }
    else if (act === 'again') { SEED = Math.floor(Math.random() * 1e9); generate(); }
    else if (act === 'edit') $('#form-card').scrollIntoView({ behavior:'smooth', block:'start' });
    else if (act === 'copy') {
      navigator.clipboard.writeText(toPlainText(CURRENT))
        .then(() => toast('コピーしました'))
        .catch(() => toast('コピーできませんでした'));
    }
  });
}

function toPlainText(program) {
  const { scheme, splitName, days, input } = program;
  const L = ['■ あなた専用のトレーニングプラン',
    `${splitName}／週${input.freq}回・1回${input.mins}分・${ENV_LABEL[input.env]}`,
    `組み方：${scheme.label}（基本 ${scheme.sets}セット × ${scheme.reps}／休憩${scheme.rest}秒）`, ''];
  days.forEach((d, i) => {
    L.push(`--- DAY ${i + 1}　${d.title}（約${d.est}分）---`);
    d.blocks.filter((b) => b.items.length).forEach((b) => {
      L.push(`[${b.title}]`);
      b.items.forEach((it) => L.push(`  ・${it.ex.name}　${doseFor(it.ex, scheme)}`));
    });
    L.push('');
  });
  L.push('※ 痛みが出る動作は中止してください。持病のある方は医師の指示を優先してください。');
  return L.join('\n');
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 2000);
}

/* ==========================================================================
   9. フォーム側のふるまい
   ========================================================================== */

function generate(quiet) {
  rand = mulberry32(SEED);                 // 同じシードからは必ず同じプランになる
  const input = readInput();
  const program = buildProgram(input, analyze(input));
  render(program, quiet);
  savePlan(input);
  return program;
}

/* 作ったプランをそのまま保存する（リロードしても同じプランが開く） */
function savePlan(input) {
  const picks = CURRENT.days.map((d) => d.blocks.map((b) => b.items.map((i) => i.ex.id)));
  try {
    localStorage.setItem('gym-menu-input', JSON.stringify(input));
    localStorage.setItem('gym-menu-plan', JSON.stringify({ seed:SEED, input, picks }));
  } catch (e) {}
}

/* 保存しておいた差し替え結果を、組み直したプランに戻す */
function applyPicks(program, picks) {
  if (!Array.isArray(picks) || picks.length !== program.days.length) return false;
  const byId = {};
  EX_DB.forEach((e) => { byId[e.id] = e; });
  return program.days.every((day, d) =>
    Array.isArray(picks[d]) && picks[d].length === day.blocks.length &&
    day.blocks.every((b, bi) =>
      Array.isArray(picks[d][bi]) && picks[d][bi].length === b.items.length &&
      b.items.every((it, i) => {
        const ex = byId[picks[d][bi][i]];
        if (ex) it.ex = ex;
        return true;
      })));
}

function syncToggles() {
  $$('.chip').forEach((l) => l.classList.toggle('on', $('input', l).checked));
  $$('.seg label').forEach((l) => l.classList.toggle('on', $('input', l).checked));
}

function updateMeta() {
  const i = readInput();
  $('#cta-meta').innerHTML =
    `<i>週${i.freq}回</i>・<i>1回${i.mins}分</i>・<i>${ENV_LABEL[i.env]}</i>` +
    (i.pains.length ? `・<i>${i.pains.map((p) => GOAL_LABEL[p].replace('にやさしく', '')).join('/')}に配慮</i>` : '');
}

function updateDetect() {
  const input = readInput();
  $('#counter').textContent = input.text.length + '字';
  const box = $('#detect');
  const an = analyze(input);
  const shown = input.text || input.chips.length || input.pains.length ? an.matched : [];
  box.innerHTML = '<span class="dt">読み取った目的</span>' + (
    shown.length
      ? shown.slice(0, 7).map((r) => `<span class="tag brand">${r.label}</span>`).join('') +
        Array.from(an.flags).map((f) => `<span class="tag line">${GOAL_LABEL[f] || f} に配慮</span>`).join('')
      : '<span class="empty">入力するとここに表示されます</span>');
}

function restorePlan() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem('gym-menu-plan') || 'null'); } catch (e) { return; }
  if (!saved || typeof saved.seed !== 'number' || !saved.input) return;
  SEED = saved.seed;
  const program = buildProgram(saved.input, analyze(saved.input));
  applyPicks(program, saved.picks);
  render(program, true);
}

function restoreForm() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem('gym-menu-input') || 'null'); } catch (e) { return; }
  if (!saved) return;
  $('#concern').value = saved.text || '';
  const setRadio = (n, v) => { const el = $(`input[name="${n}"][value="${v}"]`); if (el) el.checked = true; };
  setRadio('level', saved.level); setRadio('freq', saved.freq);
  setRadio('mins', saved.mins);   setRadio('env', saved.env);
  (saved.chips || []).forEach((v) => { const el = $(`input[name="preset"][value="${v}"]`); if (el) el.checked = true; });
  (saved.pains || []).forEach((v) => { const el = $(`input[name="pain"][value="${v}"]`); if (el) el.checked = true; });
}

/* テーマ切り替え */
function applyTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  $$('[data-theme-set]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.themeSet === mode)));
  try { localStorage.setItem('gym-menu-theme', mode); } catch (e) {}
}

/* ==========================================================================
   10. 起動
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  // 悩みチップを生成
  const wanted = ['fatloss','belly','bulk','posture','stiffness','endurance','glutes',
                  'flabbyarm','looks','stress','beginner','health','strength','legs'];
  const box = $('#preset-chips');
  wanted.forEach((id) => {
    const rule = CONCERN_RULES.find((r) => r.id === id);
    if (!rule) return;
    const label = document.createElement('label');
    label.className = 'chip';
    label.innerHTML = `<input type="checkbox" name="preset" value="${rule.id}">${rule.label}`;
    box.appendChild(label);
  });

  // テーマ
  let theme = 'auto';
  try { theme = localStorage.getItem('gym-menu-theme') || 'auto'; } catch (e) {}
  applyTheme(theme);
  $$('[data-theme-set]').forEach((b) =>
    b.addEventListener('click', () => applyTheme(b.dataset.themeSet)));

  // 入力の変化を拾う
  let t = null, ctaT = null;
  const onInput = () => {
    clearTimeout(t); t = setTimeout(() => { updateDetect(); updateMeta(); }, 120);
    const cta = $('.cta');                 // 入力中だけボタンを下げ、読み取り結果を見せる
    cta.classList.add('away');
    clearTimeout(ctaT);
    ctaT = setTimeout(() => cta.classList.remove('away'), 1100);
  };
  $('#concern').addEventListener('input', onInput);
  document.addEventListener('change', (e) => {
    if (e.target.matches('.chip input, .seg input')) { syncToggles(); updateDetect(); updateMeta(); }
  });

  $$('.sample').forEach((b) => b.addEventListener('click', () => {
    const ta = $('#concern');
    ta.value = ta.value.trim() ? ta.value.trim() + '。' + b.textContent : b.textContent;
    ta.focus();
    updateDetect(); updateMeta();
  }));

  $('#form').addEventListener('submit', (e) => { e.preventDefault(); generate(); });

  // 上部バーの境界線
  const bar = $('#topbar');
  const onScroll = () => bar.classList.toggle('stuck', window.scrollY > 8);
  window.addEventListener('scroll', onScroll, { passive:true });
  onScroll();

  RestTimer.init();
  bindResult();
  bindDayKeys();
  restoreForm();
  syncToggles();
  updateDetect();
  updateMeta();
  restorePlan();
});
