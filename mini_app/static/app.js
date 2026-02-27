const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

let currentQ = null, swiperInst = null, isOnline = navigator.onLine;

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').then(reg => {
    if ('sync' in reg) reg.sync.register('quiz-sync').catch(()=>{});
  });
}

window.addEventListener('online',  () => { isOnline=true;  toggleBanner(true);  syncQueue(); });
window.addEventListener('offline', () => { isOnline=false; toggleBanner(false); });

function toggleBanner(online) {
  document.getElementById('offlineBanner').style.display = online ? 'none' : 'block';
  if (online) {
    const b = document.getElementById('onlineBanner');
    b.style.display = 'block';
    setTimeout(() => b.style.display='none', 3000);
  }
}

function toast(msg, ms=2500) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), ms);
}

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.page===name));
  document.getElementById('page-'+name).classList.add('active');
  if (name==='home')       loadHome();
  if (name==='quiz')       loadNextQuestion();
  if (name==='flashcards') loadFlashcards();
  if (name==='analytics')  loadAnalytics();
}

async function api(path, method='GET', body=null) {
  try {
    const opts = {method, headers:{'Content-Type':'application/json'}};
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    return await r.json();
  } catch { return null; }
}

async function loadHome() {
  const [stats, ana] = await Promise.all([api('/api/stats'), api('/api/analytics')]);
  if (!stats) return;
  document.getElementById('statTotal').textContent  = stats.total || 0;
  document.getElementById('statDue').textContent    = stats.due   || 0;
  document.getElementById('statAuto').textContent   = stats.auto_captured || 0;
  document.getElementById('statStreak').textContent = ana?.streak_days || 0;
  if (ana) {
    document.getElementById('homeBadge').textContent = ana.badge || '🌱';
    document.getElementById('homeLevel').textContent = 'المستوى ' + (ana.level||1);
    document.getElementById('xpBar').style.width     = ((ana.xp||0)*10)+'%';
    document.getElementById('xpText').textContent    = 'XP: '+(ana.xp||0)+'/10';
  }
}

function prioTxt(p) {
  return {urgent:'🔥 عاجل',normal:'⚡ متوسط',low:'📖 عادي'}[p]||p;
}

async function loadNextQuestion() {
  const mode = document.getElementById('quizMode').value;
  const tag  = document.getElementById('quizTag').value;
  const data = await api('/api/questions?mode='+mode+'&limit=1'+(tag?'&tag='+tag:''));
  const box  = document.getElementById('quizContainer');
  if (!data||!data.length) {
    box.innerHTML = '<div class="card" style="text-align:center;padding:40px"><div style="font-size:48px">🎉</div><p style="margin-top:12px">لا توجد أسئلة في هذا الوضع!</p></div>';
    return;
  }
  currentQ = data[0];
  renderQuiz(currentQ, box);
}

function renderQuiz(q, box) {
  const labels = ['أ','ب','ج','د','هـ','و'];
  const hasOpts = q.options && q.options.length >= 2;
  const optsHtml = hasOpts
    ? q.options.map((o,i) =>
        `<button class="opt-btn" onclick="checkAns(${i},this)" data-index="${i}">${labels[i]||i+1}) ${o}</button>`
      ).join('')
    : '<p style="color:var(--sub);font-size:14px;">📝 سؤال مقالي — قيّمه أدناه</p>';

  box.innerHTML = `
    <div class="quiz-card">
      <div class="quiz-meta">#${q.id} | ${prioTxt(q.priority)} | مراجعات:${q.total_reviews}${q.auto_captured?' 🤖':''}${q.tags?.length?' | '+q.tags.join('،'):''}</div>
      <div class="quiz-text">${q.text.replace(/\n/g,'<br/>')}</div>
      <div class="options">${optsHtml}</div>
      ${q.explanation?`<div class="explanation" id="exp">💡 <b>الشرح:</b> ${q.explanation}</div>`:''}
      <div class="rating-btns" id="rBtns">
        <button class="rate-btn rate-again" onclick="submitReview(0)">Again</button>
        <button class="rate-btn rate-hard"  onclick="submitReview(3)">Hard</button>
        <button class="rate-btn rate-good"  onclick="submitReview(4)">Good</button>
        <button class="rate-btn rate-easy"  onclick="submitReview(5)">Easy</button>
      </div>
    </div>
    <button onclick="loadNextQuestion()" style="width:100%;padding:14px;background:var(--card);border:1px solid rgba(123,44,191,.4);border-radius:12px;color:var(--text);font-size:14px;cursor:pointer;">⏭ تخطي</button>
  `;
}

function checkAns(idx, btn) {
  const btns = document.querySelectorAll('.opt-btn');
  btns.forEach(b => b.disabled=true);
  if (idx === currentQ.correct_index) {
    btn.classList.add('correct'); toast('✅ صح! أحسنت!');
  } else {
    btn.classList.add('wrong');
    btns[currentQ.correct_index]?.classList.add('correct');
    toast('❌ خطأ! راجع الإجابة الصحيحة.');
  }
  const exp  = document.getElementById('exp');
  if (exp) exp.style.display='block';
  const rBtns = document.getElementById('rBtns');
  if (rBtns) rBtns.style.display='grid';
}

async function submitReview(quality) {
  if (!currentQ) return;
  const r = await api('/api/review','POST',{question_id:currentQ.id,quality});
  const labels = ['Again','','','Hard','Good','Easy'];
  if (r?.offline) toast('📴 '+labels[quality]+' — سيُزامَن لاحقاً');
  else toast('✅ '+(labels[quality]||'') + ' — التالي: '+(r?.next_review?.slice(0,10)||'قريباً'));
  setTimeout(() => loadNextQuestion(), 700);
}

async function loadFlashcards() {
  const data = await api('/api/questions?mode=all&limit=30');
  if (!data||!data.length) {
    document.getElementById('swiperWrapper').innerHTML =
      '<div class="swiper-slide"><div class="q-text" style="text-align:center;color:var(--sub);margin-top:40px">لا توجد أسئلة!</div></div>';
    return;
  }
  const labels = ['أ','ب','ج','د'];
  document.getElementById('swiperWrapper').innerHTML = data.map(q => {
    const opts = q.options?.length ? '<br/><br/>'+q.options.map((o,i)=>`${labels[i]||i+1}) ${o}`).join('<br/>') : '';
    return `<div class="swiper-slide">
      <div class="q-meta"><span>#${q.id} | ${prioTxt(q.priority)}</span><span>${q.tags?.join('،')||''}</span></div>
      <div class="q-text">${q.text.replace(/\n/g,'<br/>')}${opts}</div>
      <div class="swipe-hint"><span class="swipe-left">← صعب</span><span style="color:var(--sub)">اسحب للتقييم</span><span class="swipe-right">سهل →</span></div>
    </div>`;
  }).join('');
  document.getElementById('flashCounter').textContent = '1 / '+data.length;

  if (swiperInst) swiperInst.destroy(true,true);
  swiperInst = new Swiper('#mainSwiper', {
    effect:'cards', grabCursor:true, centeredSlides:true,
    on: {
      slideChange(s) {
        document.getElementById('flashCounter').textContent = (s.activeIndex+1)+' / '+data.length;
      },
      touchEnd(s) {
        const diff = s.touches.startX - s.touches.currentX;
        const q    = data[s.previousIndex];
        if (!q) return;
        if (diff > 60)  { api('/api/review','POST',{question_id:q.id,quality:0}); toast('❌ صعب — سيُراجع غداً'); }
        if (diff < -60) { api('/api/review','POST',{question_id:q.id,quality:5}); toast('✅ سهل — مؤجّل!'); }
      }
    }
  });
}

async function loadAnalytics() {
  const [ana, stats] = await Promise.all([api('/api/analytics'), api('/api/stats')]);
  if (!ana) return;
  const pred = ana.prediction || {};
  document.getElementById('predScore').textContent = pred.overall!==undefined ? pred.overall+'%' : '--';
  document.getElementById('predConf').textContent  = 'الثقة: '+(pred.confidence||'—')+' | '+( pred.total_reviewed||0)+' مراجعة';
  document.getElementById('anaRev').textContent    = ana.total_reviews||0;
  document.getElementById('anaStr').textContent    = ana.streak_days||0;
  document.getElementById('anaStrong').textContent = ana.strong_count||0;
  document.getElementById('anaWeak').textContent   = ana.weak_count||0;
  const box  = document.getElementById('tagBreak');
  const tags = Object.entries(pred.by_tag||{});
  if (!tags.length) { box.innerHTML='<p style="color:var(--sub);font-size:14px;">أضف وسوماً للأسئلة لرؤية التحليل</p>'; return; }
  box.innerHTML = tags.sort((a,b)=>b[1].score-a[1].score).map(([tag,info])=>`
    <div class="tag-row">
      <span style="font-size:14px">🏷️ ${tag}</span>
      <div class="tag-bar-wrap"><div class="tag-bar" style="width:${info.score}%"></div></div>
      <span style="font-size:16px;font-weight:700;color:${info.score>=70?'var(--green)':info.score>=50?'var(--blue)':'var(--red)'}">${info.score}%</span>
    </div>`).join('');
}

async function syncQueue() {
  try {
    const db    = await openIDB();
    const items = await getAllIDB(db);
    if (!items.length) return;
    const r = await api('/api/sync','POST',{items});
    if (r?.synced > 0) {
      toast('✅ مزامنة '+r.synced+' مراجعة');
      db.transaction('pending_reviews','readwrite').objectStore('pending_reviews').clear();
    }
  } catch {}
}

function openIDB() {
  return new Promise((res,rej) => {
    const req = indexedDB.open('quiz_offline',1);
    req.onupgradeneeded = () => req.result.createObjectStore('pending_reviews',{autoIncrement:true});
    req.onsuccess = () => res(req.result);
    req.onerror   = () => rej(req.error);
  });
}
function getAllIDB(db) {
  return new Promise(res => {
    const req = db.transaction('pending_reviews','readonly').objectStore('pending_reviews').getAll();
    req.onsuccess = () => res(req.result||[]);
  });
}

(async () => {
  if (!navigator.onLine) toggleBanner(false);
  const tags = await api('/api/tags');
  const sel  = document.getElementById('quizTag');
  (tags||[]).forEach(t => {
    const o = document.createElement('option');
    o.value=t; o.textContent='🏷️ '+t; sel.appendChild(o);
  });
  await loadHome();
})();
