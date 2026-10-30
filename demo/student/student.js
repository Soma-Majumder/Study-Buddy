'use strict';
const $=id=>document.getElementById(id);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let KEY='study-buddy-workbook-connected-v1';
let liveMode=false,paidEnabled=false;
const main=$('main');
let selectedLesson=null;
let preparationPoll=null,pollEpoch=0,actionEpoch=0,pendingStartLesson=null,pendingStartView='lesson';
let capabilities=[],compatibleApi=false;
let token,lessons=[],remote=null,busy=false,storageOK=true;
const pendingRequests=new Set();
function freshState(){return {view:'library',sessionId:null,quizId:null,requestId:null,requestLesson:null,selectedLessonId:null,selectedLessonVersion:null,index:0,answers:{}};}
let state={view:'library',sessionId:null,quizId:null,requestId:null,requestLesson:null,selectedLessonId:null,selectedLessonVersion:null,index:0,answers:{}};
function announce(text){$('live-status').textContent=text;}
function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch{storageOK=false;}}
function button(text,fn,secondary=false){const b=el('button',text,'button'+(secondary?' secondary':''));b.type='button';b.onclick=fn;return b;}
function title(text){const h=el('h1',text);h.tabIndex=-1;return h;}
function eyebrow(text){return el('p',text,'eyebrow');}
function selectedChapter(){return lessons.find(l=>l.lesson_id===state.selectedLessonId&&l.version===state.selectedLessonVersion)||null;}
function selectChapter(item){selectedLesson=item;state.selectedLessonId=item.lesson_id;state.selectedLessonVersion=item.version;save();}
function navigate(view,record=true){state.view=view;save();if(record){const suffix=view==='chapter'&&selectedChapter()?'/'+encodeURIComponent(state.selectedLessonId):'';history.pushState(null,'','#'+view+suffix);}render();window.scrollTo({top:0,behavior:'instant'});main.querySelector('h1,legend')?.focus({preventScroll:true});}
function routeFromHash(){const [view,id]=location.hash.slice(1).split('/');if(view==='chapter'&&id){const item=lessons.find(l=>l.lesson_id===decodeURIComponent(id));if(item)selectChapter(item);}if(['library','lessons','chapter','preparing','lesson','quiz','confirm','results','summary','status'].includes(view))return view;return null;}
function paidButton(text,fn,secondary=false){const b=button(text,fn,secondary);b.dataset.mutating='true';b.disabled=busy;return b;}
async function api(path,payload){const controller=new AbortController();if(path!=='/api/cancel')pendingRequests.add(controller);try{const response=await fetch(path,{method:payload?'POST':'GET',signal:controller.signal,headers:{'X-Workbook-Token':token,...(payload?{'Content-Type':'application/json'}:{})},...(payload?{body:JSON.stringify(payload)}:{})});let data;try{data=await response.json();}catch{throw Error('The server response could not be read. Reconnect before starting again.');}if(!response.ok)throw Error(data.error||'Could not complete the action.');return data;}finally{pendingRequests.delete(controller);}}
async function home(){
 const previous={session_id:state.sessionId,request_id:state.requestId};
 clearPolling();const epoch=++actionEpoch;
 for(const request of pendingRequests)request.abort();pendingRequests.clear();
 remote=null;selectedLesson=null;pendingStartLesson=null;pendingStartView='lesson';state=freshState();busyState(false);
 try{localStorage.removeItem(KEY);}catch{storageOK=false;}
 if($('reset-dialog').open)$('reset-dialog').close();
 history.replaceState(null,'','#library');render();window.scrollTo({top:0,behavior:'instant'});main.querySelector('h1')?.focus({preventScroll:true});
 if(!lessons.length)void boot();
 if(!previous.session_id&&!previous.request_id){announce('Home. Choose a chapter to begin.');return;}
 busyState(true,'Stopping the previous session…');
 try{await api('/api/cancel',previous);if(epoch===actionEpoch)announce('Previous work stopped. Choose a chapter to begin.');}
 catch{if(epoch===actionEpoch)showError(Error('Your browser has been reset, but stopping server work could not be confirmed. Reload before starting again.'));}
 finally{if(epoch===actionEpoch)busyState(false);}
}
function busyState(value,message){busy=value;document.querySelectorAll('button[data-mutating="true"]').forEach(b=>{b.disabled=value||b.dataset.unavailable==='true';});if(message)announce(message);}
function applySnapshot(data){const oldQuiz=state.quizId;remote=data;state.sessionId=data.session_id;if(data.quiz){state.quizId=data.quiz.quiz_id;if(oldQuiz!==state.quizId){state.answers={};state.index=0;}else{const allowed=new Set(data.quiz.questions.map(q=>q.question_id));state.answers=Object.fromEntries(Object.entries(state.answers).filter(([id,value])=>allowed.has(id)&&['a','b','c','d'].includes(value)));}}save();}
function initialView(route){return route||'library';}
function hasResumableSession(){return !!remote&&remote.session_id===state.sessionId&&!['failed','completed'].includes(remote.status);}
function showError(error){const old=$('connection-error');old?.remove();const note=el('section',undefined,'connection-error');note.id='connection-error';note.setAttribute('role','alert');note.append(el('h2','Let’s keep your place.'),el('p',error.message),button('Reconnect to saved progress',reconnect,true));main.prepend(note);note.scrollIntoView({block:'start'});}
function clearPolling(){pollEpoch++;clearTimeout(preparationPoll);preparationPoll=null;}
function isPreparing(data=remote){return !!data&&(['started','teaching','reteaching'].includes(data.status)||(data.status==='grading'&&!data.attempts?.at(-1)?.feedback));}
async function act(task,view,message){if(busy)return;const epoch=++actionEpoch;const origin=state.view;busyState(true,message);try{const data=await task();if(epoch!==actionEpoch)return;applySnapshot(data);busyState(false);if(isPreparing(data)){watchPreparation();if(state.view==='results'){showInlineActivity();}else if(state.view===origin||state.view==='preparing')navigate('preparing');return;}clearPolling();if(state.view===origin||state.view==='preparing'){navigate(data.status==='failed'?'status':typeof view==='function'?view(data):view);}else render();}catch(error){if(epoch!==actionEpoch)return;busyState(false);showError(error);if(state.requestId||state.sessionId)watchPreparation();}}
async function readProgress(){if(!compatibleApi&&!state.sessionId)return {pending:true};if(state.sessionId)return api('/api/session?session_id='+encodeURIComponent(state.sessionId));if(state.requestId)return api('/api/start-progress?request_id='+encodeURIComponent(state.requestId));return {pending:true};}
async function reconnect(){const epoch=actionEpoch;try{const config=await fetch('/api/config').then(r=>{if(!r.ok)throw Error('The workbook server is unavailable.');return r.json();});checkConfig(config);const data=await readProgress();if(epoch!==actionEpoch)return;if(!data.pending)applySnapshot(data);if(isPreparing()||data.pending&&state.requestId)watchPreparation();navigate(resumeView());}catch(error){if(epoch===actionEpoch)showError(error);}}
function resumeView(){if(!remote)return state.requestId?'preparing':'lessons';if(remote.status==='completed')return'summary';if(remote.status==='failed'||remote.status==='pending_save')return'status';if(isPreparing())return'preparing';if(remote.status==='grading'&&remote.attempts?.at(-1)?.feedback)return'results';if(remote.quiz)return state.nextView==='quiz'?'quiz':['quiz','confirm'].includes(state.view)?state.view:'lesson';return remote.attempts.length?'results':'status';}
function checkConfig(config){if(!['real_controller_offline_fixtures','real_controller_live_agents'].includes(config.mode))throw Error('Unexpected backend mode.');compatibleApi=config.api_version===2;token=config.token;liveMode=config.mode==='real_controller_live_agents';paidEnabled=config.paid_calls_enabled===true;capabilities=config.capabilities||[];}
function latest(){return remote?.attempts.at(-1);}
function retry(){return remote?.explanation?.round==='retry';}
function sections(){
 const text=remote?.explanation?.text||'';
 return text.split(/\n\s*\n/).filter(x=>x.trim()).map((part,i)=>{
  const heading=part.match(/^#{1,3}\s+([^\n]+)\n?/);
  return {name:heading?heading[1]:!liveMode&&remote.explanation.concepts[i]?remote.explanation.concepts[i].name:'Tutor notes · '+(i+1),text:heading?part.slice(heading[0].length):part};
 });
}
function steps(active){
 const list=el('ol',undefined,'steps');list.setAttribute('aria-label','Study session progress');
 const available=[!!remote?.explanation,!!remote?.quiz,!!latest()?.feedback||!!remote?.summary];
 ['Read','Practice','Review'].forEach((name,i)=>{
  const li=el('li',undefined,i===active?'current':'');
  const control=el('button',undefined,'step-link');control.type='button';control.disabled=busy||!available[i];
  control.setAttribute('aria-label',name+(available[i]?'':' — not ready yet'));
  if(i===active)control.setAttribute('aria-current','step');
  control.append(el('span',String(i+1).padStart(2,'0'),'step-num'),el('span',name));
  control.onclick=()=>{if(busy||!available[i])return;navigate(i===0?'lesson':i===1?'quiz':remote.summary?'summary':'results');};
  li.append(control);list.append(li);
 });return list;
}

function source(){const d=el('details',undefined,'source');d.append(el('summary','Lesson source & attribution'));if(remote){d.append(el('p',remote.lesson.attribution+' · '+remote.lesson.license+'. The reading below uses the saved Tutor explanation for this session.'));const a=el('a','Read the original chapter ↗');a.href=remote.lesson.url;a.target='_blank';a.rel='noopener';d.append(a);}return d;}
function progressKey(data){return JSON.stringify([data.session_id,data.status,data.quiz?.quiz_id,data.explanation?.round,data.attempts?.length,!!data.attempts?.at(-1)?.feedback]);}
function watchPreparation(){
 clearPolling();if(!compatibleApi)return;const epoch=pollEpoch;const session=state.sessionId,request=state.requestId;const baseline=busy&&remote?progressKey(remote):null;let failures=0;
 async function poll(){
  if(epoch!==pollEpoch||session!==state.sessionId&&session||request!==state.requestId)return;
  try{const data=await readProgress();if(epoch!==pollEpoch)return;failures=0;
   if(!data.pending&&!(busy&&baseline===progressKey(data))){applySnapshot(data);if(!isPreparing(data)){clearPolling();actionEpoch++;busyState(false);if(state.view==='preparing'||state.view==='status'||state.view==='results'&&data.explanation?.round==='retry')navigate(resumeView());else render();return;}}
   if(state.view==='preparing'||state.view==='results'){const stage=$('preparation-stage');if(stage)stage.textContent=data.preparation?.message||'Waiting for the saved start request…';const progress=$('creation-progress');if(progress)progress.dataset.phase=creationPhase(data.preparation?.stage);if(state.view==='preparing'&&data.explanation&&!$('early-explanation')&&!$('creation-progress')?.closest('.chapter-ai-option'))render();}
  }catch{if(epoch!==pollEpoch)return;failures++;if(failures>=3){clearPolling();busyState(false);showError(Error('Progress updates are unavailable. Reconnect to the existing session; do not start another paid run.'));return;}}
  if(epoch===pollEpoch)preparationPoll=setTimeout(poll,2500);
 }
 preparationPoll=setTimeout(poll,750);
}
function start(lesson=selectedChapter()||lessons[0],nextView='lesson'){if(busy||!lesson)return;state.nextView=nextView;if(liveMode&&!paidEnabled){showError(Error('Paid calls are disabled on this server. You can still read the original chapter.'));return;}selectChapter(lesson);const requestLesson=lesson.lesson_id+':'+lesson.version;if(!state.requestId||state.requestLesson!==requestLesson){state.requestId=crypto.randomUUID();state.requestLesson=requestLesson;save();}navigate('preparing');act(()=>{watchPreparation();return api('/api/start',{request_id:state.requestId,lesson_id:lesson.lesson_id,lesson_version:lesson.version});},data=>data.quiz?nextView:data.status==='completed'?'summary':'status','Preparing your AI study session…');}
async function beginNewSession(item=null,nextView='lesson'){
 const previous={session_id:state.sessionId,request_id:state.requestId};clearPolling();const epoch=++actionEpoch;
 for(const request of pendingRequests)request.abort();pendingRequests.clear();
 remote=null;selectedLesson=null;state=freshState();if(item)selectChapter(item);busyState(true,'Stopping the previous session…');navigate(item?'chapter':'lessons');
 try{if(previous.session_id||previous.request_id)await api('/api/cancel',previous);if(epoch!==actionEpoch)return;busyState(false);if(item)start(item,nextView);}
 catch(error){if(epoch===actionEpoch){busyState(false);showError(error);}}
}
function requestNewSession(item=null,nextView='lesson'){
 if(busy||isPreparing()){showError(Error('A study operation is still running. Use Home to stop it before starting another.'));return;}
 if(state.view!=='summary'||remote?.status!=='completed'||!remote?.summary){void beginNewSession(item,nextView);return;}
 pendingStartLesson=item;pendingStartView=nextView;const dialog=$('reset-dialog');
 dialog.querySelector('h2').textContent='Ready for another chapter?';
 dialog.querySelector('p:not(.eyebrow)').textContent=item?(liveMode?'Your completed summary stays saved. Start paid AI preparation for '+item.title+'.':'Your completed summary stays saved. Start practice for '+item.title+'.'):'Your completed summary stays saved. Choose your next chapter to begin a new session.';
 $('confirm-reset').textContent=item?'Start the next chapter':'Choose a chapter';dialog.showModal();
}
function creationPhase(stage){
 if(stage==='feedback')return'feedback';
 if(['generating','revising','generation_invalid','revision_invalid'].includes(stage))return'quiz';
 if(['reviewing','reviewing_revision'].includes(stage))return'review';
 return'summary';
}
function creationProgress(stage){
 const panel=el('div',undefined,'creation-progress');panel.id='creation-progress';panel.dataset.phase=creationPhase(stage);
 const bar=el('div',undefined,'creation-progress-track');bar.setAttribute('role','progressbar');bar.setAttribute('aria-label','AI preparation in progress');bar.setAttribute('aria-describedby','preparation-stage');
 const fill=el('span',undefined,'creation-progress-fill');fill.setAttribute('aria-hidden','true');bar.append(fill);
 const labels=el('ol',undefined,'creation-progress-steps');labels.setAttribute('aria-label','Preparation stages');
 [['summary','Summary'],['quiz','Quiz'],['review','Review']].forEach(([phase,label])=>{const item=el('li',label);item.dataset.phase=phase;labels.append(item);});
 if(stage==='feedback'){labels.replaceChildren(el('li','Preparing your feedback'));}
 panel.append(bar,labels);return panel;
}
function preparation(){
 const feedbackPending=remote?.status==='grading'||busy&&!!remote?.quiz;
 if(!feedbackPending&&selectedChapter()&&!remote?.attempts?.length)return chapter(true);
 if(feedbackPending){const root=el('section',undefined,'reading preparation-screen');root.append(eyebrow('YOUR QUIZ'),title('Preparing your feedback.'));const stage=el('p','Grading your answers and preparing AI feedback…','preparation-stage');stage.id='preparation-stage';stage.setAttribute('role','status');stage.setAttribute('aria-live','polite');root.append(stage,creationProgress('feedback'));main.replaceChildren(root);return;}
 const root=el('section',undefined,'reading preparation-screen');
 root.append(eyebrow('YOUR STUDY SESSION'),title(remote?.explanation?'Your explanation is ready.':'Preparing your explanation.'));
 if(remote?.explanation){
  const reading=el('article',undefined,'early-reading');reading.id='early-explanation';reading.append(el('h2',remote.lesson.title));
  remote.explanation.text.split(/\n\s*\n/).filter(Boolean).forEach(t=>reading.append(el('p',t)));root.append(reading);
 }else root.append(el('p','The explanation will appear here as soon as Tutor saves it. You do not need to wait for the quiz review.','lede'));
 const progress=el('div',undefined,'preparation-footer');
 const stage=el('p',remote?.preparation?.message||'Tutor is preparing your explanation.','preparation-stage');stage.id='preparation-stage';stage.setAttribute('role','status');stage.setAttribute('aria-live','polite');
 progress.append(stage,creationProgress(remote?.preparation?.stage),el('p','You can read while the quiz prepares. Only an approved quiz will become available.','sample-note'));
 const controls=el('div',undefined,'results-actions');controls.append(button('← Browse chapters',()=>navigate('lessons'),true),button('Reconnect to this session',reconnect,true));progress.append(controls);root.append(progress);main.replaceChildren(root);
}
function library(){

 const hero=el('section',undefined,'hero');const text=el('div',undefined,'hero-copy');text.append(eyebrow('YOUR PERSONAL STUDY SPACE'));
 const h=title('');h.append(document.createTextNode('A new idea.'),el('br'),el('em','A little more understood.'));text.append(h,el('p','Explore biology with a clear explanation, test your understanding, and find your next step.','hero-description'));
 const label='Start learning →';const heroActions=el('div',undefined,'landing-actions');heroActions.append(button(label,()=>navigate('lessons')));text.append(heroActions);
 text.append(el('p','Begin with one lesson. Leave with a clearer next step.','hero-footnote'));hero.append(text);
 const heading=el('div',undefined,'section-heading');heading.append(el('h2','A clear path. Your pace.'),el('p','From a new idea to a little more confidence.'));
 const strip=el('section',undefined,'journey-strip');[['Read','Understand the lesson.','Read the original chapter, then get an AI explanation when you want one.'],['Practice','Test your understanding.','Ten questions. Room to think. Feedback on your answers.'],['Reflect','Know your next step.','Revisit missed ideas, try once more, and save your summary.']].forEach(([tag,h,p],i)=>{const article=el('article');article.append(el('span',['✧','⌁','↗'][i],'journey-icon'),eyebrow('0'+(i+1)+' / '+tag),el('h3',h),el('p',p));strip.append(article);});
 const bottom=el('div',undefined,'library-bottom');bottom.append(el('p',liveMode?'Live Claude agents · Starting a chapter, requesting feedback, or preparing a retry uses paid calls. Opening this page does not. Your progress stays in the local workbook database.':'Offline practice mode · Real controller and saved progress, with fixture agent content. No paid calls. Retry questions may repeat.'));const landing=el('div',undefined,'landing-experience');landing.append(hero,heading,strip,bottom);main.replaceChildren(landing);if(remote?.status==='completed'&&remote.summary){const reset=el('button','Start a separate session ↗','button-link');reset.onclick=()=>requestNewSession();main.append(reset);}
}
function lessonSelection(){
 const guide={
  lesson_cell_theory:{order:0,label:'Foundations',goal:'Explore what cells have in common and how prokaryotic and eukaryotic cells differ.'},
  lesson_cell_membranes:{order:1,label:'Cell boundaries',goal:'Discover how phospholipids, proteins and cholesterol shape the cell membrane.'},
  lesson_passive_transport:{order:2,label:'Moving with a gradient',goal:'Learn how diffusion and membrane proteins move substances without cellular energy input.'},
  lesson_active_transport:{order:3,label:'Moving against a gradient',goal:'Compare ATP-powered pumps with transport that uses energy stored in a gradient.'}
 };
 const ordered=[...lessons].sort((a,b)=>(guide[a.lesson_id]?.order??99)-(guide[b.lesson_id]?.order??99)||a.title.localeCompare(b.title));
 const root=el('section',undefined,'lesson-selection');
 root.append(eyebrow('EXPLORE BIOLOGY'),title('Choose your next chapter.'),el('p','Start with cells, then explore membranes and transport. Follow this order or choose any chapter.','lede'));
 const grid=el('div',undefined,'lesson-selection-grid');
 ordered.forEach((item,i)=>{
  const info=guide[item.lesson_id];const card=el('article',undefined,'lesson-option');
  card.append(eyebrow(String(i+1).padStart(2,'0')+' / '+(info?.label||'Biology')),el('h2',item.title),el('p',info?.goal||'Read this chapter and explore its main ideas.','lesson-goal'));
  const actions=el('div',undefined,'lesson-card-actions');
  const begin=button('Read chapter →',()=>{selectChapter(item);navigate('chapter');});begin.setAttribute('aria-label','Read chapter: '+item.title);
  actions.append(begin);card.append(actions);grid.append(card);
 });
 if(!ordered.length)grid.append(el('p','No chapters are available yet.'));
 root.append(grid);
 main.replaceChildren(root);
}
function chapter(preparing=false){
 const item=selectedChapter();if(!item)return navigate('lessons');
 const same=remote?.lesson.lesson_id===item.lesson_id&&remote?.lesson.version===item.version;
 const root=el('article',undefined,'chapter-overview');
 root.append(button('← All chapters',()=>navigate('lessons'),true));
 const heading=el('header',undefined,'chapter-heading');heading.append(eyebrow('YOUR CHAPTER'),title(item.title),el('p','Choose how you want to read. Then put your understanding into practice.','lede'));root.append(heading);
 const choices=el('div',undefined,'chapter-reading-choices');
 const originalCard=el('section',undefined,'chapter-reading-option');
 originalCard.append(eyebrow('THE ORIGINAL TEXT'),el('h2','Read the full chapter'),el('p','Explore the complete chapter on the textbook’s website, in a separate tab.','chapter-choice-description'));
 const original=el('a','Open full chapter ↗','button');original.href=item.source_url;original.target='_blank';original.rel='noopener';original.setAttribute('aria-label','Open the full '+item.title+' chapter in a new tab');originalCard.append(original);
 const credits=el('div',undefined,'chapter-credits');credits.append(el('p',item.attribution),el('p',item.license));originalCard.append(credits);
 const aiCard=el('section',undefined,'chapter-reading-option chapter-ai-option');
 aiCard.append(eyebrow('THE AI EXPLANATION'),el('h2','Read the AI summary'),el('p','Get a beginner-friendly explanation of this chapter’s key ideas, followed by the 10-question quiz.','chapter-choice-description'));
 if(preparing){
  const waiting=button('Creating your AI summary…',()=>{});waiting.disabled=true;aiCard.append(waiting);
 }else if(same&&remote?.status!=='failed'&&remote?.status!=='completed'){
  aiCard.append(button('Read AI summary →',()=>{state.nextView='lesson';save();navigate(remote.quiz?'lesson':resumeView());}));
 }else{
  const begin=paidButton('Read AI summary →',()=>remote?requestNewSession(item):start(item));begin.dataset.unavailable=String(liveMode&&!paidEnabled);begin.disabled=busy||liveMode&&!paidEnabled;aiCard.append(begin);
 }
 if(preparing){const activity=el('div',undefined,'chapter-ai-notice');const stage=el('p',remote?.preparation?.message||'Creating your AI summary…');stage.id='preparation-stage';stage.setAttribute('role','status');stage.setAttribute('aria-live','polite');activity.append(stage,creationProgress(remote?.preparation?.stage));aiCard.append(activity);}
 else aiCard.append(el('p',liveMode?(same&&remote?.explanation&&remote.status!=='failed'&&remote.status!=='completed'?'Your generated explanation is saved and ready to continue.':'Preparing the summary and quiz uses paid Claude calls. Your explanation appears as soon as it is ready.'):'Offline demo: explanation and quiz use fixture content. No paid calls.','chapter-ai-notice'));
 choices.append(originalCard,aiCard);root.append(choices);
 const next=el('div',undefined,'chapter-next-step');next.append(eyebrow('NEXT / PRACTICE'),el('p','Both reading paths lead to a 10-question quiz, feedback, and one focused retry if needed.'));if(preparing){const waiting=button('Preparing your practice…',()=>{});waiting.disabled=true;next.append(waiting);}else if(same&&remote?.quiz)next.append(button('Continue to quiz →',()=>navigate('quiz')));else if(same&&isPreparing())next.append(button('See preparation progress →',()=>navigate('preparing')));else{const practice=paidButton('Continue to quiz →',()=>remote?requestNewSession(item,'quiz'):start(item,'quiz'));practice.dataset.unavailable=String(liveMode&&!paidEnabled);practice.disabled=busy||liveMode&&!paidEnabled;next.append(practice);}root.append(next);
 const details=el('details',undefined,'source chapter-study-excerpt');details.append(el('summary','Adapted study text used by the AI'));const body=el('div',undefined,'chapter-source');(item.source_text||'').split(/\n\s*\n/).filter(Boolean).forEach(text=>body.append(el('p',text)));details.append(body,el('p',item.adaptation_notice,'sample-note'));root.append(details);
 main.replaceChildren(root);
}

function lesson(){if(!remote?.explanation)return navigate('status');const layout=el('div',undefined,'workspace');const aside=el('aside',undefined,'sidebar');aside.append(eyebrow(retry()?'PRACTICE NOTES':'AI SUMMARY'),el('p',remote.lesson.title,'side-title'));aside.append(el('p','Saved to your local workbook database. Your place is also kept in this browser.','side-note'));const reading=el('article',undefined,'reading');reading.append(eyebrow(retry()?'ONE FOCUSED RETRY / REVISIT MISSED IDEAS':'BIOLOGY / AI SUMMARY'),title(retry()?'Let’s look again.':remote.lesson.title),el('p',retry()?'These notes cover the concepts you missed. Read them, then take your one focused 10-question retry.':'The most important ideas from this chapter, explained simply.','lede'));const font=el('button','Aa · Larger text','font-control');font.setAttribute('aria-pressed','false');font.onclick=()=>{const large=reading.classList.toggle('large-text');font.setAttribute('aria-pressed',String(large));font.textContent=large?'Aa · Standard text':'Aa · Larger text';};reading.append(font);const summary=el('div',undefined,'lesson-summary');sections().forEach(s=>{if(s.text.trim())summary.append(el('p',s.text));});reading.append(summary);reading.append(source());if(retry()&&!liveMode)reading.append(el('p','Offline fixture retry: questions may repeat within the missed concepts. This verifies the controller’s one-retry flow, not live question quality.','sample-note'));const actions=el('div',undefined,'bottom-action');actions.append(el('small','10 questions · No timer · Pass mark: 8 of 10'),button(Object.keys(state.answers).length?'Continue practice ↗':retry()?'Begin my one retry ↗':'Start the practice questions ↗',()=>navigate('quiz')));reading.append(actions);layout.append(aside,reading);main.replaceChildren(steps(0),layout);}
function question(){if(!remote?.quiz)return navigate(resumeView());const questions=remote.quiz.questions;const q=questions[state.index]||questions[0];const layout=el('div',undefined,'quiz-layout');const side=el('aside',undefined,'sidebar');side.append(eyebrow(retry()?'FOCUSED RETRY / 1 OF 1':'PRACTICE / FIRST ATTEMPT'),el('p','One idea at a time.','side-title'));const map=el('nav',undefined,'question-map');map.setAttribute('aria-label','Questions');questions.forEach((item,i)=>{const b=el('button',String(i+1),state.answers[item.question_id]?'answered':'');b.setAttribute('aria-label','Question '+(i+1)+(state.answers[item.question_id]?', answered':', unanswered'));if(i===state.index)b.setAttribute('aria-current','step');b.onclick=()=>{state.index=i;navigate('quiz');};map.append(b);});side.append(map,el('p','You can change any answer before submitting. Nothing is timed.','side-note'));if(!storageOK)side.append(el('p','Browser storage unavailable; keep this tab open.','side-note'));const read=el('button','↖ Return to the chapter','button-link');read.onclick=()=>navigate('lesson');side.append(read);const area=el('section',undefined,'question-area');const top=el('div',undefined,'question-top');top.append(eyebrow((retry()?'RETRY · ':'')+'QUESTION '+String(state.index+1).padStart(2,'0')+' / 10'),el('span',Object.keys(state.answers).length+' of 10 answered'));const progress=el('div',undefined,'question-progress');progress.setAttribute('role','progressbar');progress.setAttribute('aria-label','Questions answered');progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax','10');progress.setAttribute('aria-valuenow',String(Object.keys(state.answers).length));const fill=el('div');fill.style.width=Object.keys(state.answers).length*10+'%';progress.append(fill);area.append(top,progress);const field=el('fieldset');const legend=el('legend',q.prompt);legend.tabIndex=-1;field.append(legend);Object.entries(q.options).forEach(([key,text])=>{const label=el('label',undefined,'choice');const input=el('input');input.type='radio';input.name=q.question_id;input.value=key;input.checked=state.answers[q.question_id]===key;const letter=el('span',key.toUpperCase(),'choice-letter');letter.setAttribute('aria-hidden','true');label.append(input,letter,el('span',text,'choice-text'),el('span','Selected','selected-mark'));input.onchange=()=>{state.answers[q.question_id]=key;save();const n=Object.keys(state.answers).length;top.lastChild.textContent=n+' of 10 answered';fill.style.width=n*10+'%';progress.setAttribute('aria-valuenow',String(n));map.children[state.index].classList.add('answered');map.children[state.index].setAttribute('aria-label','Question '+(state.index+1)+', answered');announce('Answer '+key.toUpperCase()+' selected.');};field.append(label);});area.append(field);const hint=el('p','Choose one answer. Your choices are sent only when you press Submit quiz.','quiz-hint');hint.id='question-hint';const actions=el('div',undefined,'quiz-actions');actions.append(button('← Back',()=>{if(state.index>0){state.index--;navigate('quiz');}else navigate('lesson');},true),button(state.index===9?'Submit quiz →':'Next question →',()=>{if(!state.answers[q.question_id]){hint.textContent='Choose an answer before continuing.';announce(hint.textContent);field.querySelector('input').focus();return;}if(state.index<9){state.index++;navigate('quiz');}else submitQuiz();}));area.append(actions,hint);layout.append(side,area);main.replaceChildren(steps(1),layout);}
function submitQuiz(){
 if(busy||!remote?.quiz)return;
 const missing=remote.quiz.questions.findIndex(q=>!['a','b','c','d'].includes(state.answers[q.question_id]));
 if(missing!==-1){state.index=missing;navigate('quiz');announce('Answer every question before submitting.');return;}
 const submission={session_id:state.sessionId,quiz_id:remote.quiz.quiz_id,answers:{...state.answers}};
 const operation=act(()=>{watchPreparation();return api('/api/submit',submission);},'results','Grading your quiz and preparing AI feedback…');
 navigate('preparing');return operation;
}
// Old saved confirmation links return to the quiz without submitting anything.
function confirm(){state.index=9;navigate('quiz');}

function showInlineActivity(){
 const reserved=main.querySelector('.inline-ai-activity');
 if(reserved){reserved.classList.remove('activity-idle');reserved.removeAttribute('aria-hidden');return;}
 if($('creation-progress'))return;
 const root=main.querySelector('.feedback-results');if(!root)return;
 const activity=el('div',undefined,'inline-ai-activity');
 const stage=el('p',remote?.preparation?.message||(latest()?.round==='first'&&latest()?.score<8?'Tutor is explaining the missed concepts and preparing your retry…':'Saving your session summary…'));
 stage.id='preparation-stage';stage.setAttribute('role','status');stage.setAttribute('aria-live','polite');
 activity.append(stage,creationProgress(remote?.preparation?.stage));
 const actions=root.querySelector('.results-actions');actions?.after(activity);
}
function continueSession(){if(busy)return;const operation=act(()=>{watchPreparation();return api('/api/continue',{session_id:state.sessionId});},data=>data.status==='completed'?'summary':data.quiz?'lesson':'status','Following the controller’s next step and saving progress…');showInlineActivity();return operation;}
function results(){const attempt=latest();if(!attempt)return navigate('status');const n=attempt.score;const root=el('section',undefined,'results feedback-results');const hero=el('div',undefined,'results-hero');const copy=el('div');copy.append(eyebrow(attempt.round==='retry'?'FEEDBACK / YOUR ONE RETRY':'FEEDBACK / FIRST ATTEMPT'),title(n>=8?'Your ideas are taking shape.':'A few notes for next time.'),el('p',attempt.feedback?.summary||'Your answers are saved. Reconnect to check feedback progress.','lede'));const counter=el('div');const number=el('div',String(n),'score');number.append(el('small','/10'));counter.append(number,el('p','CORRECT · SERVER-GRADED','score-caption'));hero.append(copy,counter);root.append(hero);const info=el('div',undefined,'result-info');info.append(el('h2',n>=8?'Keep these ideas with you.':'Review the marked questions.'),el('p',attempt.round==='retry'?'This is your final attempt for this session. The next step saves your summary.':n>=8?'You reached the 8-of-10 pass mark. No retry is needed.':'After reading your feedback, revisit the missed concepts and take one focused retry.'));root.append(info);const explanations=new Map((attempt.feedback?.feedback||[]).map(x=>[x.position,x.explanation]));[...attempt.items].sort((a,b)=>Number(a.is_correct)-Number(b.is_correct)).forEach(q=>{const d=el('details',undefined,'review-item'+(q.is_correct?'':' missed'));d.open=!q.is_correct;const summary=el('summary');summary.append(el('span',String(q.position).padStart(2,'0'),'section-index'),el('span',q.prompt),el('span',q.is_correct?'Correct':'Revisit','review-badge'));const body=el('div',undefined,'review-details');const selected=el('p',undefined,'answer-detail');selected.append(el('span','Your answer','answer-label'),el('span',q.options[q.selected]));body.append(selected);if(!q.is_correct){const correct=el('p',undefined,'answer-detail');correct.append(el('span','Correct answer','answer-label'),el('span',q.options[q.correct_option]));body.append(correct);}body.append(el('p',explanations.get(q.position)||q.rationale,'rationale'));d.append(summary,body);root.append(d);});const actions=el('div',undefined,'results-actions');if(remote.status==='completed')actions.append(button('Open saved summary ↗',()=>navigate('summary')));else if(remote.quiz)actions.append(button('Continue the saved retry ↗',()=>navigate('lesson')));else actions.append(paidButton(attempt.round==='first'&&n<8?(liveMode?'Reteach & prepare retry with Claude ↗':'Review missed ideas & prepare retry ↗'):'Save my session summary ↗',continueSession));root.append(actions);{const activity=el('div',undefined,'inline-ai-activity'+(busy||isPreparing()?'':' activity-idle'));if(!busy&&!isPreparing())activity.setAttribute('aria-hidden','true');const stage=el('p',remote?.preparation?.message||(attempt.round==='first'&&n<8?'Tutor is explaining the missed concepts and preparing your retry…':'Saving your session summary…'));stage.id='preparation-stage';stage.setAttribute('role','status');stage.setAttribute('aria-live','polite');activity.append(stage,creationProgress(remote?.preparation?.stage));root.append(activity);}root.append(el('p',liveMode?'Server-graded answers · Real Claude feedback · Only a quiz that passes the controller’s review gate reaches practice.':'Real saved feedback and scores · Offline fixture content · No paid agent calls.','sample-note'));main.replaceChildren(steps(2),root);}
function summary(){if(!remote?.summary)return navigate('status');const saved=remote.summary;const root=el('section',undefined,'results session-summary');root.append(eyebrow('CHAPTER COMPLETE / SAVED WORK'),title('Your chapter, in review.'),el('p',saved.message,'lede'));const card=el('div',undefined,'summary-card');card.append(eyebrow('YOUR WORKBOOK RECORD'),el('h2',saved.lesson.title));const scores=el('dl',undefined,'summary-scores');saved.attempts.forEach(a=>{const row=el('div',undefined,'summary-score-row');row.append(el('dt',a.round==='first'?'First attempt':'Focused retry'),el('dd',a.score+' / '+a.out_of));scores.append(row);});card.append(scores);if(saved.concepts_to_revisit.length){card.append(el('h3','Ideas to revisit','summary-concepts-title'));const ul=el('ul',undefined,'summary-concepts');saved.concepts_to_revisit.forEach(c=>ul.append(el('li',c.name)));card.append(ul);}else card.append(el('p','Revisit the chapter to reinforce these ideas.'));card.append(el('p','Learning retries used: '+saved.learning_retry_count+' of 1.','sample-note'));root.append(card);const actions=el('div',undefined,'results-actions');actions.append(button('Back to the study room ↗',()=>void home()),button('Download my summary ↓',downloadSummary,true));root.append(actions);const reset=el('button','Open a new workbook session','button-link');reset.onclick=()=>requestNewSession();actions.append(reset);root.append(source());main.replaceChildren(steps(2),root);}
function quizRegenerationReason(){
 if(remote?.failure?.code==='review_invalid_report')return 'The AI quiz review returned a response the system could not read or validate. This does not mean your answers were wrong. The quiz was not shown because its quality check could not finish. Generate the quiz again to try a fresh preparation attempt.';
 if(remote?.failure?.code==='review_uncertainty')return 'The quality check could not confidently verify that every question has one clear answer supported by your lesson. We held back this quiz so you are not tested on uncertain content. Generate a new quiz to try again.';
 if(remote?.failure?.code==='content_revision_exhausted')return 'The quiz still had question-quality problems after its correction attempt, so it was not shown. Generate a new quiz to get a fresh set of questions for this lesson.';
 return 'The quiz could not be completed. You can try generating it again.';
}
function canGenerateQuizAgain(){return remote?.status==='failed'&&['review_uncertainty','content_revision_exhausted','review_invalid_report'].includes(remote.failure?.code);}
function generateQuizAgain(){
 if(busy||!canGenerateQuizAgain())return;
 const item=lessons.find(l=>l.lesson_id===remote.lesson.lesson_id&&l.version===remote.lesson.version);
 if(!item){showError(Error('This lesson version is unavailable. Refresh the chapter list.'));return;}
 if(liveMode&&!paidEnabled){showError(Error('Paid calls are disabled on this server.'));return;}
 return beginNewSession(item,'lesson');
}
function status(){const root=el('section',undefined,'results');root.append(eyebrow('WORKBOOK PROGRESS'),title(canGenerateQuizAgain()?'Let’s create a better quiz.':remote?.status==='failed'?'Let’s pause here.':'Your work is saved.'),el('p',remote?.status==='failed'?(canGenerateQuizAgain()?quizRegenerationReason():remote.failure?.message||'This session stopped before it could finish. Your saved progress is preserved.'):remote?.message||'Read the saved status before continuing.','lede'));if(remote?.status==='pending_save')root.append(paidButton('Retry saving the same summary ↗',continueSession));else if(remote?.status==='reteaching')root.append(paidButton('Continue the reserved retry ↗',continueSession));else if(!canGenerateQuizAgain())root.append(button('Read saved progress ↗',reconnect));if(canGenerateQuizAgain()){const retryQuiz=paidButton('Generate quiz again →',generateQuizAgain);retryQuiz.dataset.unavailable=String(liveMode&&!paidEnabled);retryQuiz.disabled=busy||liveMode&&!paidEnabled;root.append(retryQuiz,el('p',liveMode?'Starts new paid AI preparation for this chapter, recreating the summary and quiz. Your previous session stays saved.':'Starts a new offline preparation attempt for this chapter.','sample-note'));}if(remote?.status==='failed'&&remote.explanation){const details=el('details',undefined,'source saved-tutor-text');details.append(el('summary','Read the saved Tutor explanation'));remote.explanation.text.split(/\n\s*\n/).filter(Boolean).forEach(t=>details.append(el('p',t)));root.append(details);}if(remote?.status==='failed'&&!canGenerateQuizAgain()){const b=button('Start a new session',()=>requestNewSession(),true);root.append(b);}main.replaceChildren(root);}
function downloadSummary(){const s=remote.summary;const text='Study Buddy — saved local workbook\n'+s.lesson.title+'\n\n'+s.attempts.map(a=>(a.round==='first'?'First attempt':'Focused retry')+': '+a.score+'/10').join('\n')+'\nRetries used: '+s.learning_retry_count+'/1\nRevisit: '+(s.concepts_to_revisit.map(c=>c.name).join(', ')||'the chapter to reinforce these ideas')+'\n'+s.message+'\n\nSource: '+s.lesson.attribution+' · '+s.lesson.license+(liveMode?'\nReal Claude agents.\n':'\nOffline fixture agents; no paid calls.\n');const url=URL.createObjectURL(new Blob([text],{type:'text/plain'}));const a=el('a');a.href=url;a.download='study-buddy-workbook-summary.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);announce('Summary downloaded.');}
function render(){if(remote&& !['library','lessons','chapter'].includes(state.view)){if(['failed','pending_save'].includes(remote.status))state.view='status';else if(remote.status==='completed')state.view='summary';else if(state.view==='preparing'&&!isPreparing()&&!busy)state.view=resumeView();}if(!remote&&!['library','lessons','chapter','preparing'].includes(state.view))state.view='library';if(remote&&!remote.quiz&&['lesson','quiz','confirm'].includes(state.view))state.view=remote.status==='completed'?'summary':latest()?'results':'status';$('library-nav').classList.toggle('active',state.view==='library');const screens={library,lessons:lessonSelection,chapter,preparing:preparation,lesson,quiz:question,confirm,results,summary,status};(screens[state.view]||library)();if(!compatibleApi){const note=el('p','The server needs a restart to enable the latest progress and recovery updates. Existing chapters and saved sessions remain available.','compatibility-note');note.setAttribute('role','status');main.prepend(note);}}
document.querySelectorAll('.wordmark,.footer-brand').forEach(a=>a.onclick=event=>{event.preventDefault();void home();});$('library-nav').onclick=()=>void home();$('how-button').onclick=()=>$('about-dialog').showModal();document.querySelector('.dialog-close').onclick=()=>$('about-dialog').close();$('cancel-reset').onclick=()=>$('reset-dialog').close();$('confirm-reset').onclick=()=>{const item=pendingStartLesson,destination=pendingStartView;pendingStartLesson=null;pendingStartView='lesson';clearPolling();actionEpoch++;remote=null;state={view:'lessons',sessionId:null,quizId:null,requestId:null,requestLesson:null,selectedLessonId:item?.lesson_id||null,selectedLessonVersion:item?.version||null,index:0,answers:{}};save();$('reset-dialog').close();if(item)start(item,destination);else navigate('lessons');};
window.addEventListener('popstate',()=>{const view=routeFromHash();if(view)navigate(view,false);});

function restoreDraft(){try{const raw=JSON.parse(localStorage.getItem(KEY)||'null');if(raw&&typeof raw==='object'){const allowed=['library','lessons','chapter','preparing','lesson','quiz','confirm','results','summary','status'];state={view:allowed.includes(raw.view)?raw.view:'library',nextView:raw.nextView==='quiz'?'quiz':'lesson',sessionId:typeof raw.sessionId==='string'?raw.sessionId:null,quizId:typeof raw.quizId==='string'?raw.quizId:null,requestId:typeof raw.requestId==='string'?raw.requestId:null,requestLesson:typeof raw.requestLesson==='string'?raw.requestLesson:null,selectedLessonId:typeof raw.selectedLessonId==='string'?raw.selectedLessonId:null,selectedLessonVersion:Number.isInteger(raw.selectedLessonVersion)?raw.selectedLessonVersion:null,index:Number.isInteger(raw.index)&&raw.index>=0&&raw.index<10?raw.index:0,answers:raw.answers&&typeof raw.answers==='object'&&!Array.isArray(raw.answers)?Object.fromEntries(Object.entries(raw.answers).filter(([k,v])=>/^q_[a-z0-9]{12}$/.test(k)&&['a','b','c','d'].includes(v))):{}};}}catch{storageOK=false;}}
async function boot(){
 const epoch=actionEpoch;try{const response=await fetch('/api/config');if(!response.ok)throw Error('Open the workbook through the local backend server.');const config=await response.json();if(epoch!==actionEpoch)return;checkConfig(config);KEY='study-buddy-workbook-'+config.mode+'-v1';restoreDraft();
 $('connection-label').textContent=liveMode?(paidEnabled?'REAL AGENTS · PAID CLAUDE':'REAL AGENTS · CALLS DISABLED'):'LOCAL BACKEND · OFFLINE AGENTS';
 $('backend-description').textContent=liveMode?'This workbook uses the real Tutor, Assessment and Reviewer Claude adapters, plus server grading and saved SQLite progress. Paid calls run when you start a chapter, submit for feedback, or deliberately prepare a focused retry. The controller limits repairs and allows one learning retry. Answer keys remain on the server until submission. This is a trusted local profile, not public account authentication.':'This workbook uses offline fixture agents and a synthetic passing review report. Real controller grading, one focused retry and SQLite saving are connected; no paid calls.';
 lessons=(await api('/api/lessons')).lessons;if(epoch!==actionEpoch)return;const route=routeFromHash();state.view=initialView(route);if(state.sessionId||state.requestId){try{const data=await readProgress();if(epoch!==actionEpoch)return;if(!data.pending)applySnapshot(data);if(isPreparing()||data.pending&&state.requestId)watchPreparation();}catch(error){if(!['library','lessons','chapter'].includes(state.view))throw error;announce('Previous session could not be loaded. You can still browse chapters.');}}selectedLesson=selectedChapter();render();
 }catch(error){if(epoch!==actionEpoch)return;main.replaceChildren(eyebrow('BACKEND CONNECTION'),title('Your workbook is waiting.'),el('p',error.message,'empty-message'),button('Try connecting again',()=>location.reload()));if(token&&lessons.length)main.append(button('Open a new workbook session',()=>requestNewSession(),true));}
}
boot();
