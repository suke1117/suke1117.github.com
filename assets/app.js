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
let rand = mulberry32(Date.now());

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

/* 種目ごとの負荷を文章にする */
function doseFor(ex, scheme) {
  if (ex.unit === 'time' || ex.dose) return ex.dose;
  const isolation = ['arms_bi','arms_tri','shoulder_lat','rear_delt','accessory','core'].includes(ex.pattern);
  let sets = scheme.sets, reps = scheme.reps, rest = scheme.rest;
  if (isolation) {
    sets = Math.max(2, Math.min(scheme.sets, 3));
    if (scheme.key === 'strength') { reps = '8〜10回'; rest = 75; }
    else if (scheme.key === 'hyper') { reps = '12〜15回'; rest = 60; }
    else rest = Math.min(rest, 60);
  }
  return `${sets}セット × ${reps}（休憩${rest}秒）`;
}

/* おおよその所要時間（分） */
function timeFor(ex, scheme) {
  if (ex.dose) {                                   // 所要時間が固定の種目
    const m = /(\d+)\s*分/.exec(ex.dose);
    if (m) return parseInt(m[1], 10);
    return ['mobility', 'stretch', 'warmup'].includes(ex.pattern) ? 2 : 3;
  }
  const isolation = ['arms_bi','arms_tri','shoulder_lat','rear_delt','accessory','core'].includes(ex.pattern);
  const sets = isolation ? Math.max(2, Math.min(scheme.sets, 3)) : scheme.sets;
  const rest = isolation ? Math.min(scheme.rest, 60) : scheme.rest;
  return Math.round((sets * (40 + rest)) / 60);
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

/* --------------------------------------------------------- 8. 描画 */

let CURRENT = null;

function itemHTML(item, scheme, dayIdx, blockIdx, itemIdx) {
  const ex = item.ex;
  const why = (ex.goals || [])
    .filter((g) => (CURRENT.an.score[g] || 0) >= 2.5 && GOAL_LABEL[g])
    .slice(0, 3).map((g) => GOAL_LABEL[g]).join('・');
  return `
    <li class="ex" data-d="${dayIdx}" data-b="${blockIdx}" data-i="${itemIdx}">
      <div class="ex-main">
        <div class="ex-name">${ex.name}</div>
        <div class="ex-dose">${doseFor(ex, scheme)}</div>
        ${ex.note ? `<div class="ex-note">${ex.note}</div>` : ''}
        ${why ? `<div class="ex-why">狙い：${why}</div>` : ''}
      </div>
      ${item.alts && item.alts.length > 1 ? `<button class="swap noprint" type="button" title="別の種目に差し替える">差し替え</button>` : ''}
    </li>`;
}

function render(program) {
  CURRENT = program;
  const { scheme, splitName, days, input, an } = program;
  const topGoals = an.ranked.filter((g) => GOAL_LABEL[g] && an.score[g] >= 2.5).slice(0, 6);

  const summary = `
    <section class="card summary-head">
      <h2>あなた専用のトレーニングプラン</h2>
      <p>${splitName}／週${input.freq}回・1回${input.mins}分・${ENV_LABEL[input.env]}</p>
      <div class="tags">
        ${topGoals.map((g) => `<span class="tag">${GOAL_LABEL[g]}</span>`).join('')}
        ${Array.from(an.flags).map((f) => `<span class="tag alt">${GOAL_LABEL[f] || f}</span>`).join('')}
      </div>
      <div class="facts">
        <dl class="fact"><dt>組み方</dt><dd>${scheme.label}</dd></dl>
        <dl class="fact"><dt>基本セット</dt><dd>${scheme.sets}セット × ${scheme.reps}</dd></dl>
        <dl class="fact"><dt>セット間の休憩</dt><dd>${scheme.rest}秒</dd></dl>
      </div>
      <p class="ex-note" style="margin-top:12px">${scheme.desc}</p>
    </section>`;

  const readMsgs = an.matched.length
    ? an.matched.slice(0, 5).map((r) => `<li><strong>${r.label}：</strong>${r.msg}</li>`).join('')
    : `<li>具体的な悩みが読み取れなかったため、全身をバランスよく鍛える標準プランを組みました。気になる部位を入力すると、そこに寄せたメニューになります。</li>`;

  const reading = `
    <section class="card">
      <h2>あなたの悩みから読み取ったこと</h2>
      <ul class="note-list">${readMsgs}</ul>
    </section>`;

  const dayCards = days.map((d, di) => `
    <section class="card">
      <div class="day-head">
        <h3><span class="day-no">DAY ${di + 1}</span>${d.title}</h3>
        <span class="meta">${d.day ? d.day + '曜 目安 / ' : ''}約${d.est}分</span>
      </div>
      ${d.blocks.filter((b) => b.items.length).map((b, bi) => `
        <div class="block">
          <h4>${b.title}</h4>
          ${b.ordered ? '<ol class="ex-list">' : '<ul class="ex-list">'}
            ${b.items.map((it, ii) => itemHTML(it, scheme, di, bi, ii)).join('')}
          ${b.ordered ? '</ol>' : '</ul>'}
          ${b.note ? `<div class="ex-why">${b.note}</div>` : ''}
        </div>`).join('')}
    </section>`).join('');

  const weekNote = `
    <section class="card">
      <h2>続けるためのポイント</h2>
      <ul class="note-list">${progressionNotes(program).map((n) => `<li>${n}</li>`).join('')}</ul>
      <div class="callout">
        <strong>安全のために</strong>
        ${an.cautions.map((c) => `<div>・${c}</div>`).join('')}
        <div>・痛みが出る動作は中止してください。「効いている感覚」と「関節の痛み」は別物です。</div>
        <div>・このプランは一般的な運動指導の範囲の情報です。持病・服薬・妊娠中・リハビリ中の方は医師の指示を優先してください。</div>
      </div>
    </section>`;

  $('#result').innerHTML = `
    <div class="actions noprint">
      <button class="btn btn-ghost" id="btn-copy">テキストでコピー</button>
      <button class="btn btn-ghost" id="btn-print">印刷 / PDF保存</button>
      <button class="btn btn-ghost" id="btn-again">別パターンで再生成</button>
      <button class="btn btn-ghost" id="btn-edit">条件を変える</button>
    </div>
    ${summary}${reading}${dayCards}${weekNote}`;
  $('#result').hidden = false;

  bindActions();
  $('#result').scrollIntoView({ behavior:'smooth', block:'start' });
}

/* 種目の差し替え（#result への委譲で1回だけ登録する） */
function handleSwap(btn) {
  const li = btn.closest('.ex');
  const d = +li.dataset.d, b = +li.dataset.b, i = +li.dataset.i;
  const item = CURRENT.days[d].blocks[b].items[i];
  const idx = item.alts.findIndex((e) => e.id === item.ex.id);
  const usedToday = new Set(
    CURRENT.days[d].blocks.flatMap((bl) => bl.items.map((it) => it.ex.id)));
  const next = item.alts.find((e, k) => k > idx && !usedToday.has(e.id))
    || item.alts.find((e) => e.id !== item.ex.id && !usedToday.has(e.id));
  if (!next) { toast('ほかに条件に合う種目がありません'); return; }
  item.ex = next;
  li.outerHTML = itemHTML(item, CURRENT.scheme, d, b, i);
  toast('種目を差し替えました');
}

/* 結果カード上のボタン（描画のたびに要素ごと差し替わるので重複登録にならない） */
function bindActions() {
  $('#btn-print').addEventListener('click', () => window.print());
  $('#btn-again').addEventListener('click', () => {
    rand = mulberry32(Math.floor(Math.random() * 1e9));
    generate();
  });
  $('#btn-edit').addEventListener('click', () => {
    $('#form-card').scrollIntoView({ behavior:'smooth', block:'start' });
  });
  $('#btn-copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(toPlainText(CURRENT));
      toast('コピーしました');
    } catch (e) { toast('コピーできませんでした'); }
  });
}

function toPlainText(program) {
  const { scheme, splitName, days, input } = program;
  const L = [];
  L.push('■ あなた専用のトレーニングプラン');
  L.push(`${splitName}／週${input.freq}回・1回${input.mins}分・${ENV_LABEL[input.env]}`);
  L.push(`組み方：${scheme.label}（基本 ${scheme.sets}セット × ${scheme.reps}／休憩${scheme.rest}秒）`);
  L.push('');
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
  toast._t = setTimeout(() => t.classList.remove('show'), 1800);
}

/* --------------------------------------------------------- 9. 起動 */

function generate() {
  const input = readInput();
  const an = analyze(input);
  render(buildProgram(input, an));
  try { localStorage.setItem('gym-menu-input', JSON.stringify(input)); } catch (e) {}
}

function restore() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem('gym-menu-input') || 'null'); } catch (e) { return; }
  if (!saved) return;
  $('#concern').value = saved.text || '';
  const setRadio = (name, v) => {
    const el = $(`input[name="${name}"][value="${v}"]`);
    if (el) { el.checked = true; }
  };
  setRadio('level', saved.level); setRadio('freq', saved.freq);
  setRadio('mins', saved.mins);   setRadio('env', saved.env);
  (saved.chips || []).forEach((v) => { const el = $(`input[name="preset"][value="${v}"]`); if (el) el.checked = true; });
  (saved.pains || []).forEach((v) => { const el = $(`input[name="pain"][value="${v}"]`); if (el) el.checked = true; });
  syncToggles();
}

// チップ／セグメントの見た目を状態に同期
function syncToggles() {
  $$('.chip').forEach((l) => l.classList.toggle('on', $('input', l).checked));
  $$('.seg label').forEach((l) => l.classList.toggle('on', $('input', l).checked));
}

document.addEventListener('DOMContentLoaded', () => {
  // プリセットの悩みチップを生成
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

  document.addEventListener('change', (e) => {
    if (e.target.matches('.chip input, .seg input')) syncToggles();
  });

  $('#result').addEventListener('click', (e) => {
    const btn = e.target.closest('.swap');
    if (btn) handleSwap(btn);
  });

  $('#gen').addEventListener('click', (e) => { e.preventDefault(); generate(); });
  $('#form').addEventListener('submit', (e) => { e.preventDefault(); generate(); });

  restore();
  syncToggles();
});
