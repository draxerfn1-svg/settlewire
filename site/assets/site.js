/* ============================================================
   SettleWire shared runtime — loaded by every page.
   ============================================================ */

/* ---------- config ---------- */
/* Paste your form endpoint here to activate signups (2-min setup):
   formspree.io -> new form -> copy the URL like https://formspree.io/f/abcd1234
   Works with any endpoint accepting a JSON POST of {email}. */
const NEWSLETTER_ENDPOINT = '';

const CATS = ["Trading","Legal","Deals","Data","Tech","Global","Opinion","Stocks"];

const TICKS = [
 ["FED CUT SEPT","62%","up"],["BTC $70K JULY","20%","dn"],["FRANCE WC WIN","35%","up"],
 ["KALSHI IPO '27","41%","up"],["PUTIN OUT '26","17%","dn"],["SPACEX $2T DEBUT","61%","up"],
 ["EU RETAIL ACCESS","22%","dn"],["CFTC RULE FINAL Q3","54%","up"],["LEBRON → GSW","60%","up"]
];

const EVENTS = [
 {d:"07",m:"Jul",kind:"Court",txt:"Cert petition window closes on the Third Circuit ruling — potential first Supreme Court look at event-contract regulation."},
 {d:"15",m:"Jul",kind:"Court",txt:"Ninth Circuit ruling window opens in the Nevada platforms case. A loss creates a circuit split."},
 {d:"21",m:"Jul",kind:"Earnings",txt:"Interactive Brokers Q2 call — watch event-contract volume guidance and rulemaking commentary."},
 {d:"30",m:"Jul",kind:"Court",txt:"Fourth Circuit ruling window in the Maryland sports-contract appeal. Could deepen or resolve the split."},
 {d:"04",m:"Aug",kind:"Regulatory",txt:"CFTC comment window on the proposed event-contract classification framework is scheduled to close."},
 {d:"12",m:"Aug",kind:"Earnings",txt:"Robinhood Q2 call — first full quarter with index event contracts in the product mix."},
 {d:"19",m:"Aug",kind:"Regulatory",txt:"ESMA workshop on applying existing EU retail rules to event-contract venues."},
 {d:"28",m:"Aug",kind:"Court",txt:"Status conference in the state sports-contract injunction — fine accrual schedule on the docket."}
];

/* ---------- helpers ---------- */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function ago(iso){
  const m = Math.max(1, Math.round((Date.now() - new Date(iso)) / 60000));
  if (m < 60) return m + 'm ago';
  const h = Math.round(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.round(h / 24) + 'd ago';
}
const qs = new URLSearchParams(location.search);

/* Normalize stories.json into render-ready objects (HTML-escaped). */
function fetchStories(){
  return fetch('stories.json')
    .then(r => { if(!r.ok) throw 0; return r.json(); })
    .then(d => (d.stories || []).map(s => ({
      ...s,
      headline: esc(s.headline), lede: esc(s.lede), why: esc(s.why),
      category: esc(s.category),
      slug: s.slug || s.id,
      srcName: esc(s.primary_source?.name || 'source'),
      srcUrl: s.primary_source?.url || '#',
      alsoN: (s.also || []).length
    })));
}

/* ---------- shared render partials ---------- */
function rowHTML(s, i){
  return `<a class="lrow" href="story/${s.slug}.html">
    <span class="numb">${String(i+1).padStart(2,'0')}</span>
    <div><h3>${s.headline}</h3>
    <div class="row"><span class="stamp ${s.category.toLowerCase()}">${s.category}</span><span class="src">via <b>${s.srcName}</b></span><span class="time">${ago(s.published)}</span></div></div>
  </a>`;
}
function cardHTML(s){
  const alsoTxt = s.alsoN ? `+${s.alsoN} outlet${s.alsoN>1?'s':''}` : 'first on record';
  return `<article class="card">
    <div class="stub"><span class="stamp ${s.category.toLowerCase()}">${s.category}</span><span class="time">${ago(s.published)}</span></div>
    <div class="bodyc">
      <h3><a href="story/${s.slug}.html">${s.headline}</a></h3>
      <button class="whybtn" onclick="this.closest('.card').classList.toggle('open')">Why it matters</button>
      <p class="why">${s.why}</p>
      <div class="foot"><span class="src">First reported by <b><a href="${s.srcUrl}" target="_blank" rel="noopener">${s.srcName}</a></b> · ${alsoTxt}</span></div>
    </div>
  </article>`;
}
function voidHTML(label){
  return `<div class="voidt"><span class="stampv">VOID</span><p>No ${label} tickets on the tape yet — the presses run hourly.</p></div>`;
}
function skelHTML(n, h){
  return Array.from({length:n}, () => `<div class="skel" style="height:${h}px"></div>`).join('');
}
function swapIn(el){
  el.classList.remove('fade-swap'); void el.offsetWidth; el.classList.add('fade-swap');
}

/* ---------- boot: runs on every page ---------- */
document.addEventListener('DOMContentLoaded', () => {

  /* ticker */
  const tt = document.getElementById('tickerTrack');
  if (tt){
    const seq = TICKS.map(([n,p,d]) =>
      `<span class="tk">${n} <b>${p}</b> <span class="${d}">${d==='up'?'▲':'▼'}</span></span>`).join('');
    tt.innerHTML = seq + seq;
  }

  /* category rail: mark the current page's chip */
  const here = (qs.get('c') || '').toLowerCase();
  const path = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.cats-in .chip').forEach(a => {
    const target = (a.dataset.cat || '').toLowerCase();
    if ((path === 'category.html' && target === here) ||
        (path === 'index.html' && a.dataset.cat === 'Home') ||
        (path === '' && a.dataset.cat === 'Home')) a.classList.add('on');
  });
  document.querySelectorAll('nav.main a').forEach(a => {
    if (a.getAttribute('href') === path) a.classList.add('here');
  });

  /* header behaviors */
  const hdr = document.querySelector('header.site');
  addEventListener('scroll', () => {
    hdr && hdr.classList.toggle('scrolled', scrollY > 8);
    const tp = document.getElementById('totop');
    tp && tp.classList.toggle('show', scrollY > 600);
  }, {passive:true});

  const mb = document.getElementById('menuBtn');
  mb && mb.addEventListener('click', () => {
    document.getElementById('mmenu').classList.toggle('open');
    mb.setAttribute('aria-expanded', document.getElementById('mmenu').classList.contains('open'));
  });

  document.querySelectorAll('form.searchf').forEach(f => {
    f.addEventListener('submit', e => {
      e.preventDefault();
      const v = f.querySelector('input').value.trim();
      location.href = 'all.html' + (v ? '?q=' + encodeURIComponent(v) : '');
    });
  });

  /* scroll reveals */
  if ('IntersectionObserver' in window){
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); }
    }), {threshold:.12});
    document.querySelectorAll('.rv').forEach(el => io.observe(el));
  } else {
    document.querySelectorAll('.rv').forEach(el => el.classList.add('in'));
  }
});

/* ---------- back to top ---------- */
function toTop(){ scrollTo({top:0, behavior:'smooth'}); }

/* ---------- newsletter (honest: posts to a real endpoint or says so) ---------- */
async function pushEmail(email){
  if (!NEWSLETTER_ENDPOINT) return {ok:false, unconfigured:true};
  try{
    const r = await fetch(NEWSLETTER_ENDPOINT, {method:'POST',
      headers:{'Content-Type':'application/json','Accept':'application/json'},
      body: JSON.stringify({email})});
    return {ok:r.ok};
  }catch{ return {ok:false}; }
}
async function handleSub(email, form){
  const res = await pushEmail(email);
  if (res.ok){
    window.__subscribed = true;
    if (form) form.reset();
    document.getElementById('slidein')?.classList.remove('show');
    toastMsg('Subscribed — see you at noon ET.');
  } else if (res.unconfigured){
    toastMsg('Signups open soon.', true);
  } else {
    toastMsg("That didn't go through — try again.", true);
  }
  return res.ok;
}
function subscribe(e, form){
  e.preventDefault();
  const input = form.querySelector('input');
  if (!input.value.includes('@')){
    form.classList.add('shake');
    form.addEventListener('animationend', () => form.classList.remove('shake'), {once:true});
    return;
  }
  handleSub(input.value, form);
}
function toastMsg(msg, err){
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.style.background = err ? 'var(--no)' : 'var(--yes)';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}
