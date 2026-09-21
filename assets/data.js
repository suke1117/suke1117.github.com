/* ==========================================================================
   data.js — 種目データベースと「悩み」解析ルール
   すべてブラウザ内で完結。外部APIは使いません。
   ========================================================================== */

/* 器具環境:
   full     … フルジム（フリーウェイト＋マシン）
   machine  … マシン中心のジム
   dumbbell … ダンベル（＋ベンチ）が使える
   body     … 自重のみ（器具なし）                                        */

/* pattern … 動作パターン。1日のメニューはこの枠を埋める形で組み立てる。
   goals   … その種目が効く「悩みタグ」
   level   … 1:初心者OK 2:慣れてきたら 3:上級
   avoid   … その部位に痛みがある人には出さない ('knee'|'lowback'|'shoulder') */

const EX_DB = [
  /* ---------------- ウォームアップ / 有酸素 ---------------- */
  { id:'wu_bike', name:'エアロバイク（軽め）', pattern:'warmup', equip:['full','machine'], goals:['fatloss','endurance','health'], level:1, unit:'time', dose:'5分', note:'会話ができる強さで。心拍と関節を温める。' },
  { id:'wu_walk', name:'トレッドミル早歩き', pattern:'warmup', equip:['full','machine'], goals:['fatloss','endurance','health'], level:1, unit:'time', dose:'5分', note:'傾斜3〜5%、少し息が弾む速度で。' },
  { id:'wu_body', name:'その場もも上げ＋腕回し', pattern:'warmup', equip:['dumbbell','body'], goals:['fatloss','endurance'], level:1, unit:'time', dose:'3〜5分', note:'体が温まって汗ばむ手前まで。' },
  { id:'wu_squat', name:'自重スクワット（ウォームアップ）', pattern:'warmup', equip:['full','machine','dumbbell','body'], goals:[], level:1, unit:'reps', dose:'15回 × 2セット', note:'股関節と膝の可動域を出す。反動は使わない。' },

  { id:'cd_incline', name:'トレッドミル傾斜ウォーク', pattern:'cardio', equip:['full','machine'], goals:['fatloss','health','endurance'], level:1, unit:'time', dose:'15〜20分', note:'傾斜8〜10%・時速5km前後。膝への負担が少なく脂肪燃焼向き。' },
  { id:'cd_bike', name:'バイク（一定ペース）', pattern:'cardio', equip:['full','machine'], goals:['fatloss','endurance','lowback','knee'], level:1, unit:'time', dose:'15〜20分', note:'腰・膝が不安な人でも続けやすい有酸素。' },
  { id:'cd_row', name:'ローイングマシン', pattern:'cardio', equip:['full','machine'], goals:['fatloss','endurance','back'], level:2, unit:'time', dose:'10〜15分', note:'背中も同時に使える。腰を丸めないこと。' },
  { id:'cd_hiit', name:'バイクHIIT（20秒全力／40秒流す）', pattern:'cardio', equip:['full','machine'], goals:['fatloss','endurance'], level:3, unit:'time', dose:'8〜10セット', note:'短時間で追い込みたい日に。週2回まで。' },
  { id:'cd_step', name:'その場ステップ／もも上げ', pattern:'cardio', equip:['dumbbell','body'], goals:['fatloss','endurance'], level:1, unit:'time', dose:'30秒 × 8セット（間に30秒休憩）', note:'器具なしで心拍を上げる。マンションなら足音に注意。' },
  { id:'cd_burpee', name:'バーピー', pattern:'cardio', equip:['dumbbell','body'], goals:['fatloss','endurance','stress'], level:3, unit:'reps', dose:'8回 × 4セット', note:'きつければジャンプなしでOK。' },

  /* ---------------- モビリティ / 姿勢リセット ---------------- */
  { id:'mb_catcow', name:'キャット＆カウ', pattern:'mobility', equip:['full','machine','dumbbell','body'], goals:['posture','lowback','stiffness'], level:1, unit:'reps', dose:'10往復', note:'背骨を1つずつ動かすイメージ。呼吸を止めない。' },
  { id:'mb_wallslide', name:'ウォールスライド（壁沿い腕上げ）', pattern:'mobility', equip:['full','machine','dumbbell','body'], goals:['posture','stiffness','shoulder'], level:1, unit:'reps', dose:'10回 × 2セット', note:'巻き肩・猫背のリセット。腰が反らないよう注意。' },
  { id:'mb_thoracic', name:'胸椎回旋ストレッチ（四つ這い）', pattern:'mobility', equip:['full','machine','dumbbell','body'], goals:['posture','stiffness','lowback'], level:1, unit:'reps', dose:'左右10回ずつ', note:'デスクワークで固まった背中まわりをほぐす。' },
  { id:'mb_hipflex', name:'腸腰筋ストレッチ（ランジ姿勢）', pattern:'mobility', equip:['full','machine','dumbbell','body'], goals:['posture','lowback','legs'], level:1, unit:'time', dose:'左右30秒ずつ', note:'座り姿勢で縮んだ股関節前側を伸ばす。反り腰・腰痛対策。' },
  { id:'mb_chintuck', name:'チンタック（あご引き）', pattern:'mobility', equip:['full','machine','dumbbell','body'], goals:['stiffness','posture'], level:1, unit:'reps', dose:'10回 × 2セット', note:'首を後ろに引いて二重あごを作る。スマホ首・肩こりに。' },
  { id:'mb_scap', name:'肩甲骨まわし', pattern:'mobility', equip:['full','machine','dumbbell','body'], goals:['stiffness','posture'], level:1, unit:'reps', dose:'前後10回ずつ', note:'肩甲骨を大きく動かす。肩こりの血流改善に。' },

  /* ---------------- スクワット系（膝支配） ---------------- */
  { id:'sq_barbell', name:'バーベルスクワット', pattern:'squat', equip:['full'], goals:['hypertrophy','strength','legs','fatloss'], level:3, avoid:['knee','lowback'], note:'太もも・お尻・体幹をまとめて鍛える王様種目。' },
  { id:'sq_goblet', name:'ゴブレットスクワット（ダンベル）', pattern:'squat', equip:['full','dumbbell'], goals:['legs','fatloss','hypertrophy','beginner'], level:1, avoid:['knee'], note:'胸の前でダンベルを保持。フォームを覚えやすい。' },
  { id:'sq_legpress', name:'レッグプレス', pattern:'squat', equip:['full','machine'], goals:['legs','hypertrophy','beginner','knee'], level:1, note:'軌道が固定されていて安全。腰が浮くほど深く下ろさない。' },
  { id:'sq_hack', name:'スミスマシンスクワット', pattern:'squat', equip:['full','machine'], goals:['legs','hypertrophy','strength'], level:2, avoid:['knee'], note:'バランスを取る必要がなく、脚に集中できる。' },
  { id:'sq_body', name:'自重スクワット', pattern:'squat', equip:['full','machine','dumbbell','body'], goals:['legs','beginner','fatloss','health'], level:1, note:'椅子に座る軌道で。膝がつま先より大きく前に出ないように。' },
  { id:'sq_bulgarian', name:'ブルガリアンスクワット', pattern:'squat', equip:['full','dumbbell','body'], goals:['legs','glutes','hypertrophy'], level:2, avoid:['knee'], note:'片脚ずつ。お尻と太ももに強烈に効く。ふらつくなら壁に手を添える。' },
  { id:'sq_lunge', name:'ウォーキングランジ', pattern:'lunge', equip:['full','dumbbell','body'], goals:['legs','glutes','fatloss','endurance'], level:2, avoid:['knee'], note:'歩幅は大きめ。後ろ脚の膝を床すれすれまで。' },
  { id:'sq_stepup', name:'ステップアップ（台昇降）', pattern:'lunge', equip:['full','dumbbell','body'], goals:['legs','glutes','fatloss','beginner'], level:1, note:'膝の高さの台へ。上る脚だけで体を持ち上げる。' },
  { id:'sq_split', name:'スプリットスクワット（その場ランジ）', pattern:'lunge', equip:['full','machine','dumbbell','body'], goals:['legs','glutes','beginner'], level:1, avoid:['knee'], note:'前後に足を開いて上下する。移動しないぶん安定する。' },

  /* ---------------- ヒンジ系（股関節） ---------------- */
  { id:'hg_rdl', name:'ルーマニアンデッドリフト', pattern:'hinge', equip:['full','dumbbell'], goals:['glutes','legs','hypertrophy','posture','strength'], level:2, avoid:['lowback'], note:'膝は軽く曲げたまま、お尻を後ろに引いて下ろす。背中は丸めない。' },
  { id:'hg_deadlift', name:'デッドリフト', pattern:'hinge', equip:['full'], goals:['strength','hypertrophy','back','glutes'], level:3, avoid:['lowback'], note:'全身を使う高強度種目。フォームが崩れたらその場で終了。' },
  { id:'hg_hipthrust', name:'ヒップスラスト', pattern:'hinge', equip:['full','dumbbell'], goals:['glutes','legs','posture'], level:2, note:'ベンチに肩甲骨を乗せ、お尻を天井へ。ヒップアップの主力種目。' },
  { id:'hg_bridge', name:'ヒップリフト（グルートブリッジ）', pattern:'hinge', equip:['full','machine','dumbbell','body'], goals:['glutes','lowback','posture','beginner'], level:1, note:'仰向けで腰を持ち上げる。上で1秒止めてお尻を締める。' },
  { id:'hg_legcurl', name:'レッグカール', pattern:'hinge', equip:['full','machine'], goals:['legs','hypertrophy','knee'], level:1, note:'もも裏を鍛えて膝まわりを安定させる。' },
  { id:'hg_goodmorning', name:'バックエクステンション（腰の伸展）', pattern:'hinge', equip:['full','machine','body'], goals:['lowback','posture','back'], level:1, note:'反らせすぎない。背骨が一直線になるところで止める。' },

  /* ---------------- 水平プッシュ（胸） ---------------- */
  { id:'ph_bench', name:'ベンチプレス', pattern:'push_h', equip:['full'], goals:['hypertrophy','strength','chest','bulk'], level:2, avoid:['shoulder'], note:'胸板づくりの定番。肩甲骨を寄せて下ろす。' },
  { id:'ph_dbpress', name:'ダンベルプレス', pattern:'push_h', equip:['full','dumbbell'], goals:['hypertrophy','chest','bulk'], level:1, avoid:['shoulder'], note:'可動域を広く取れる。胸の真ん中を絞るイメージ。' },
  { id:'ph_machine', name:'チェストプレス（マシン）', pattern:'push_h', equip:['full','machine'], goals:['hypertrophy','chest','beginner','bulk'], level:1, note:'軌道が安定していて初心者でも追い込みやすい。' },
  { id:'ph_pushup', name:'プッシュアップ（腕立て伏せ）', pattern:'push_h', equip:['full','machine','dumbbell','body'], goals:['chest','beginner','fatloss','bulk'], level:1, avoid:['shoulder'], note:'きつければ膝つき・台に手をついて角度をつける。' },
  { id:'ph_fly', name:'ダンベルフライ／ペックフライ', pattern:'push_h', equip:['full','machine','dumbbell'], goals:['chest','hypertrophy'], level:2, avoid:['shoulder'], note:'肘を軽く曲げたまま大きく開く。ストレッチを感じる位置で切り返す。' },
  { id:'ph_dip', name:'ディップス（平行棒）', pattern:'push_h', equip:['full'], goals:['chest','arms','hypertrophy'], level:3, avoid:['shoulder'], note:'胸と三頭を同時に。肩が痛む人は避ける。' },

  /* ---------------- 垂直プッシュ（肩） ---------------- */
  { id:'pv_shoulder', name:'ショルダープレス（ダンベル）', pattern:'push_v', equip:['full','dumbbell'], goals:['shoulders','hypertrophy','bulk'], level:2, avoid:['shoulder'], note:'肘を真横ではなく少し前に。腰を反らせない。' },
  { id:'pv_machine', name:'ショルダープレス（マシン）', pattern:'push_v', equip:['full','machine'], goals:['shoulders','hypertrophy','beginner'], level:1, avoid:['shoulder'], note:'肩の土台づくり。軽い重量から。' },
  { id:'pv_lateral', name:'サイドレイズ', pattern:'shoulder_lat', equip:['full','machine','dumbbell'], goals:['shoulders','hypertrophy','posture','looks'], level:1, note:'軽い重量で肘から上げる。肩幅を出して逆三角形のシルエットに。' },
  { id:'pv_pike', name:'パイクプッシュアップ', pattern:'push_v', equip:['dumbbell','body'], goals:['shoulders','bulk'], level:2, avoid:['shoulder'], note:'腰を高く上げた腕立て。自重で肩を鍛える。' },
  { id:'pv_shrug', name:'シュラッグ（軽め・可動域重視）', pattern:'shoulder_lat', equip:['full','machine','dumbbell'], goals:['stiffness','shoulders'], level:1, note:'肩をすくめて2秒キープ、ストンと脱力。肩こりの血流改善に。' },

  /* ---------------- 垂直プル（背中） ---------------- */
  { id:'lv_latpull', name:'ラットプルダウン', pattern:'pull_v', equip:['full','machine'], goals:['back','hypertrophy','posture','beginner','looks'], level:1, note:'胸を張って鎖骨に向けて引く。広背筋を広げて逆三角形へ。' },
  { id:'lv_pullup', name:'懸垂（チンニング）', pattern:'pull_v', equip:['full'], goals:['back','strength','hypertrophy','looks'], level:3, note:'できなければアシストマシンやゴムバンドを使う。' },
  { id:'lv_bandpull', name:'タオル／チューブ ラットプル', pattern:'pull_v', equip:['dumbbell','body'], goals:['back','posture','beginner'], level:1, note:'頭上でタオルやチューブを引き下ろし、肩甲骨を下げる意識で。' },

  /* ---------------- 水平プル（背中・姿勢） ---------------- */
  { id:'lh_seatedrow', name:'シーテッドロウ（マシン）', pattern:'pull_h', equip:['full','machine'], goals:['back','posture','hypertrophy','beginner','stiffness'], level:1, note:'肩甲骨を寄せてから引く。猫背・巻き肩の改善に直結。' },
  { id:'lh_dbrow', name:'ワンハンドダンベルロウ', pattern:'pull_h', equip:['full','dumbbell'], goals:['back','hypertrophy','posture'], level:1, note:'ベンチに片手をつき、肘をお腹の横へ引き上げる。' },
  { id:'lh_barbellrow', name:'ベントオーバーロウ', pattern:'pull_h', equip:['full'], goals:['back','hypertrophy','strength'], level:3, avoid:['lowback'], note:'上体を45度前傾。腰が丸まるなら重量を下げる。' },
  { id:'lh_inverted', name:'インバーテッドロウ（斜め懸垂）', pattern:'pull_h', equip:['full','dumbbell','body'], goals:['back','posture','beginner'], level:2, note:'テーブルの縁やバーで。体を一直線に保つ。' },
  { id:'lh_facepull', name:'フェイスプル', pattern:'rear_delt', equip:['full','machine'], goals:['posture','stiffness','shoulders'], level:1, note:'顔の高さに引き、肘を後ろへ。巻き肩・肩こりの特効薬。' },
  { id:'lh_reverse', name:'リアレイズ（リバースフライ）', pattern:'rear_delt', equip:['full','machine','dumbbell'], goals:['posture','stiffness','shoulders'], level:1, note:'前傾して腕を横に開く。背中の上部と後ろ肩を使う。' },
  { id:'lh_wy', name:'うつ伏せW・Yレイズ', pattern:'rear_delt', equip:['dumbbell','body'], goals:['posture','stiffness'], level:1, note:'うつ伏せで腕をW→Yに動かす。器具なしで背中上部を刺激。' },

  /* ---------------- 腕 ---------------- */
  { id:'ar_curl', name:'ダンベルカール', pattern:'arms_bi', equip:['full','dumbbell'], goals:['arms','hypertrophy','bulk'], level:1, note:'肘を固定して振り上げない。下ろすときもゆっくり。' },
  { id:'ar_hammer', name:'ハンマーカール', pattern:'arms_bi', equip:['full','dumbbell'], goals:['arms','hypertrophy'], level:1, note:'手のひらを内向きに。腕を太く見せたいときに。' },
  { id:'ar_cablecurl', name:'ケーブルカール／マシンカール', pattern:'arms_bi', equip:['full','machine'], goals:['arms','hypertrophy','beginner'], level:1, note:'負荷が抜けにくく、初心者でも効かせやすい。' },
  { id:'ar_pushdown', name:'トライセプスプレスダウン', pattern:'arms_tri', equip:['full','machine'], goals:['arms','hypertrophy','flabbyarm'], level:1, note:'二の腕の裏側（振袖部分）を狙う。肘を体側に固定。' },
  { id:'ar_overhead', name:'オーバーヘッドエクステンション', pattern:'arms_tri', equip:['full','dumbbell'], goals:['arms','flabbyarm','hypertrophy'], level:1, avoid:['shoulder'], note:'頭の後ろで肘を伸ばす。二の腕の長頭にストレッチをかける。' },
  { id:'ar_kickback', name:'キックバック', pattern:'arms_tri', equip:['full','dumbbell'], goals:['arms','flabbyarm'], level:1, note:'軽い重量で、肘を伸ばしきって1秒静止。' },
  { id:'ar_benchdip', name:'ベンチディップス', pattern:'arms_tri', equip:['full','machine','dumbbell','body'], goals:['arms','flabbyarm','beginner'], level:1, avoid:['shoulder'], note:'椅子に手をついて体を沈める。器具なしで二の腕裏に効く。' },
  { id:'ar_narrow', name:'ナロープッシュアップ', pattern:'arms_tri', equip:['dumbbell','body'], goals:['arms','flabbyarm','chest'], level:2, note:'手幅を狭く。脇を締めて肘を後ろに引く。' },

  /* ---------------- 体幹 ---------------- */
  { id:'co_plank', name:'プランク', pattern:'core', equip:['full','machine','dumbbell','body'], goals:['belly','core','lowback','posture','beginner'], level:1, unit:'time', dose:'30〜45秒 × 3セット', note:'お尻を締めて一直線。腰が落ちたら終了の合図。' },
  { id:'co_sideplank', name:'サイドプランク', pattern:'core', equip:['full','machine','dumbbell','body'], goals:['belly','core','lowback','waist'], level:2, unit:'time', dose:'左右30秒ずつ × 2セット', note:'くびれをつくる腹斜筋と、腰を守る側方の安定性に。' },
  { id:'co_deadbug', name:'デッドバグ', pattern:'core', equip:['full','machine','dumbbell','body'], goals:['core','lowback','posture','belly','beginner'], level:1, note:'腰を床に押しつけたまま手脚を伸ばす。腰痛予防の定番。' },
  { id:'co_birddog', name:'バードドッグ', pattern:'core', equip:['full','machine','dumbbell','body'], goals:['lowback','core','posture'], level:1, note:'四つ這いで対角の手脚を伸ばす。体幹の安定を学ぶ。' },
  { id:'co_legraise', name:'レッグレイズ', pattern:'core', equip:['full','machine','dumbbell','body'], goals:['belly','core'], level:2, avoid:['lowback'], note:'下腹部狙い。腰が浮くなら膝を曲げて可動域を狭くする。' },
  { id:'co_crunch', name:'ケーブルクランチ', pattern:'core', equip:['full','machine'], goals:['belly','core','hypertrophy'], level:2, note:'腹筋も負荷をかけて鍛える。おへそを覗き込むように丸める。' },
  { id:'co_abroller', name:'アブローラー（膝コロ）', pattern:'core', equip:['full','dumbbell','body'], goals:['belly','core'], level:3, avoid:['lowback'], note:'腰を反らせない範囲まで。戻れる距離から始める。' },
  { id:'co_mountain', name:'マウンテンクライマー', pattern:'core', equip:['full','machine','dumbbell','body'], goals:['belly','fatloss','endurance','core'], level:2, unit:'time', dose:'30秒 × 3セット', note:'腹筋と心拍を同時に。お尻を上げすぎない。' },

  /* ---------------- 補助 ---------------- */
  { id:'ac_abduction', name:'ヒップアブダクション', pattern:'accessory', equip:['full','machine'], goals:['glutes','knee','lowback','legs'], level:1, note:'お尻の横（中臀筋）。骨盤が安定して腰・膝が守られる。' },
  { id:'ac_clam', name:'クラムシェル（貝殻エクササイズ）', pattern:'accessory', equip:['dumbbell','body'], goals:['glutes','lowback','knee'], level:1, note:'横向きで膝を開く。器具なしで中臀筋を刺激。' },
  { id:'ac_calf', name:'カーフレイズ', pattern:'accessory', equip:['full','machine','dumbbell','body'], goals:['legs','endurance'], level:1, note:'ふくらはぎ。第二の心臓、むくみ対策にも。' },
  { id:'ac_farmer', name:'ファーマーズウォーク', pattern:'accessory', equip:['full','dumbbell'], goals:['core','posture','endurance','strength'], level:2, unit:'time', dose:'30秒 × 3セット', note:'重い物を持って歩くだけ。握力・体幹・姿勢がまとめて鍛えられる。' },

  /* ---------------- クールダウン ---------------- */
  { id:'st_ham', name:'もも裏ストレッチ', pattern:'stretch', equip:['full','machine','dumbbell','body'], goals:['lowback','legs'], level:1, unit:'time', dose:'左右30秒ずつ', note:'もも裏が硬いと骨盤が引っぱられ腰痛の原因に。' },
  { id:'st_chest', name:'大胸筋ストレッチ（壁・ドア）', pattern:'stretch', equip:['full','machine','dumbbell','body'], goals:['posture','stiffness','chest'], level:1, unit:'time', dose:'左右30秒ずつ', note:'胸の前側を伸ばして巻き肩をリセット。' },
  { id:'st_glute', name:'お尻ストレッチ（figure-4）', pattern:'stretch', equip:['full','machine','dumbbell','body'], goals:['lowback','glutes','legs'], level:1, unit:'time', dose:'左右30秒ずつ', note:'仰向けで足を組み、手前に引き寄せる。' },
  { id:'st_neck', name:'首・僧帽筋ストレッチ', pattern:'stretch', equip:['full','machine','dumbbell','body'], goals:['stiffness','posture'], level:1, unit:'time', dose:'左右30秒ずつ', note:'頭を横に倒し、反対の肩は下げたまま。肩こりに直接効く。' },
  { id:'st_quad', name:'もも前ストレッチ', pattern:'stretch', equip:['full','machine','dumbbell','body'], goals:['legs','posture','knee'], level:1, unit:'time', dose:'左右30秒ずつ', note:'立って足首を持ち、膝を後ろへ。骨盤は立てたまま。' },
];

/* ==========================================================================
   悩みの解析ルール
   自由記述をキーワードでマッチし、goal タグ・注意フラグを立てる。
   ========================================================================== */

const CONCERN_RULES = [
  { id:'fatloss', label:'体脂肪を落とす', goals:['fatloss','belly','endurance'],
    kw:['痩せ','やせ','ヤセ','減量','ダイエット','脂肪','体脂肪','太っ','ふとっ','デブ','体重','絞','しぼり','贅肉','ぜい肉','細く','スリム','くびれ','見た目','かっこよく','かっこいい'],
    msg:'体脂肪を落とすには「筋トレで筋肉を守りつつ、有酸素と食事で消費を上回らせる」のが最短ルートです。' },

  { id:'belly', label:'お腹まわり', goals:['belly','core','fatloss'],
    kw:['お腹','おなか','腹','ぽっこり','ポッコリ','ビール腹','腹筋','シックスパック','ウエスト','くびれ','中年太り'],
    msg:'お腹は部分痩せができません。腹筋種目で土台を作りつつ、全身の消費カロリーを上げるのが正解です。' },

  { id:'bulk', label:'体を大きくする', goals:['hypertrophy','bulk','chest','back','shoulders','arms'],
    kw:['大きく','でかく','デカく','筋肉','筋肥大','バルク','ガタイ','厚み','胸板','マッチョ','増量','たくまし','逞し','太く','がっしり','細い','貧弱','ガリ'],
    msg:'筋肉を増やすには「重さを少しずつ伸ばす」「タンパク質を体重×1.6g以上」「寝る」の3点セットが必須です。' },

  { id:'strength', label:'力を強くする', goals:['strength','hypertrophy'],
    kw:['強く','パワー','重量','記録','MAX','力が','力を','高重量','伸ばしたい'],
    msg:'筋力向上は低回数・高重量・長め休憩が基本。フォームが崩れる重さには手を出さないでください。' },

  { id:'posture', label:'姿勢・猫背', goals:['posture','back','stiffness','core'],
    kw:['姿勢','猫背','ねこぜ','巻き肩','まき肩','反り腰','そり腰','背中が丸','デスクワーク','在宅','パソコン','スマホ首','ストレートネック'],
    msg:'姿勢は「背中側の筋肉を鍛える＋前側を伸ばす」の組み合わせで変わります。胸を開く意識を1日中持つのも効果的です。' },

  { id:'stiffness', label:'肩こり・首こり', goals:['stiffness','posture','back'],
    kw:['肩こり','肩凝','首こり','首が','肩が重','肩が痛','こり','コリ','凝り','頭痛','眼精'],
    msg:'肩こりは「動かさないこと」が原因の大半。血流を上げる軽い運動と、背中上部のトレーニングが効きます。',
    caution:'しびれや強い痛みを伴う場合は、トレーニングの前に整形外科を受診してください。' },

  { id:'lowback', label:'腰の不安', goals:['lowback','core','glutes'], flag:'lowback',
    kw:['腰痛','腰が','腰の','ぎっくり','ヘルニア','坐骨'],
    msg:'腰に不安があるうちは、腰を丸めず体幹を固める種目から。デッドリフトなど高リスク種目は外しています。',
    caution:'痛みが出ている最中は自己判断でのトレーニングを避け、まず医療機関で診てもらってください。' },

  { id:'knee', label:'膝の不安', goals:['knee','legs'], flag:'knee',
    kw:['膝','ひざ','ヒザ','半月板','脚が痛'],
    msg:'膝に不安がある場合は、深く曲げる種目を避けてマシン中心・もも裏とお尻の強化から入ります。',
    caution:'膝に痛みが出る動きは中止してください。腫れや引っかかりがあるなら受診を優先しましょう。' },

  { id:'shoulderpain', label:'肩の不安', goals:['shoulder','posture'], flag:'shoulder',
    kw:['四十肩','五十肩','肩を痛','肩の痛み','腱板','肩を上げると'],
    msg:'肩に不安があるため、頭上で押す種目や深く下ろす種目を外し、引く動作中心で組んでいます。',
    caution:'肩は一度こじらせると長引きます。痛みが出る角度は無理に通さないでください。' },

  { id:'endurance', label:'体力・スタミナ', goals:['endurance','fatloss','health'],
    kw:['体力','スタミナ','疲れ','つかれ','疲労','息切れ','すぐ疲','持久','走れ','階段','だるい','ダルい','年齢','老化','衰え'],
    msg:'体力は心肺と筋持久力の両輪。休憩を短めにした全身トレと、週2回の有酸素で数週間で変わります。' },

  { id:'glutes', label:'ヒップアップ', goals:['glutes','legs'],
    kw:['お尻','おしり','ヒップ','尻','垂れ','桃尻','骨盤'],
    msg:'お尻は「股関節を伸ばす種目（ヒップスラスト・RDL）」で最も育ちます。回数より収縮の意識を。' },

  { id:'legs', label:'脚', goals:['legs','glutes'],
    kw:['脚','足','太もも','ふともも','下半身','ふくらはぎ','むくみ','セルライト'],
    msg:'下半身は体の筋肉の6〜7割。鍛えると代謝が上がり、むくみも取れやすくなります。' },

  { id:'flabbyarm', label:'二の腕', goals:['flabbyarm','arms'],
    kw:['二の腕','にのうで','振袖','たぷたぷ','タプタプ','腕が太','腕の'],
    msg:'二の腕の裏側（上腕三頭筋）を鍛えると、腕のラインが引き締まって見えます。' },

  { id:'arms', label:'腕を太く', goals:['arms','hypertrophy','bulk'],
    kw:['腕を太','上腕','力こぶ','ちからこぶ','腕トレ','前腕'],
    msg:'腕は大きな種目のあとに仕上げで。週2回、合計10〜15セットが目安です。' },

  { id:'chest', label:'胸', goals:['chest','hypertrophy','bulk'],
    kw:['胸','胸板','大胸筋','ベンチプレス','バスト'],
    msg:'胸は「押す」動作。角度を変えて（水平・傾斜）刺激を散らすと発達しやすくなります。' },

  { id:'back', label:'背中', goals:['back','posture','hypertrophy'],
    kw:['背中','はみ肉','ハミ肉','広背筋','逆三角','背筋'],
    msg:'背中は引く動作で。垂直（ラットプル）と水平（ロウ）の2方向を必ず入れています。' },

  { id:'shoulders', label:'肩幅', goals:['shoulders','looks','hypertrophy'],
    kw:['肩幅','なで肩','逆三角形','シルエット','スーツ'],
    msg:'肩の横（三角筋中部）を鍛えるとシルエットが変わります。サイドレイズは軽い重量で回数を稼ぐのがコツ。' },

  { id:'stress', label:'ストレス・睡眠', goals:['stress','endurance','health','fatloss'],
    kw:['ストレス','イライラ','メンタル','気分','落ち込','不安','眠れ','睡眠','不眠','寝つき','リフレッシュ','うつ'],
    msg:'運動はメンタルに効きます。強度よりも「やり切った感」と継続が大事なので、少し息が上がる程度で十分です。' },

  { id:'beginner', label:'運動習慣づくり', goals:['beginner','health','fatloss','endurance'],
    kw:['初めて','はじめて','初心者','続かな','つづかな','挫折','運動不足','サボ','習慣','何をすれば','わからない','分からない','ジムデビュー','久しぶり','ブランク'],
    msg:'最初の4週間はフォームを覚える期間。重量を追わず「毎週ジムに行けた」を成功条件にしてください。' },

  { id:'health', label:'健康・数値改善', goals:['health','fatloss','endurance'],
    kw:['健康診断','血圧','血糖','コレステロール','中性脂肪','肝','メタボ','医者','数値','γ','尿酸'],
    msg:'数値の改善は有酸素と筋トレの併用が最も効果的。特に下半身の大きな筋肉を使うと血糖の処理が良くなります。',
    caution:'治療中の疾患がある場合は、運動の強度について主治医に確認してください。' },

  { id:'looks', label:'見た目・自信', goals:['looks','hypertrophy','fatloss','shoulders','back'],
    kw:['モテ','自信','写真','服が','服を','夏まで','結婚式','海','水着','異性','印象'],
    msg:'見た目のインパクトは「肩幅」「背中の広がり」「お腹の引き締まり」で決まります。そこを優先して組みました。' },
];
