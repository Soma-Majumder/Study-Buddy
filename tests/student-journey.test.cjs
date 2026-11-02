const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('demo/student/student.js','utf8');
function harness(draft={}){
 const nodes=new Map();const timers=new Map();let timer=0;
 function node(){return {dataset:{},children:[],disabled:false,append(...n){this.children.push(...n);},replaceChildren(...n){this.children=n;},setAttribute(){},querySelector(){return null;},querySelectorAll(){return[];},classList:{toggle(){},add(){}},focus(){}};}
 const context={console,crypto:{randomUUID:()=>`request_${++timer}`},URL,Blob,Map,Set,Error,Object,JSON,Number,String,document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},createElement:()=>node(),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[]},localStorage:{getItem:()=>JSON.stringify(draft),setItem(){}},location:{hash:''},history:{pushState(_a,_b,value){context.location.hash=value;},replaceState(_a,_b,value){context.location.hash=value;}},window:{scrollTo(){}},setTimeout:(fn)=>{const id=++timer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)};
 vm.createContext(context);
 const head=source.slice(0,source.indexOf("document.querySelectorAll('.wordmark"));
 const restore=source.slice(source.indexOf('function restoreDraft()'),source.indexOf('async function boot()'));
 vm.runInContext(head+'\n'+restore+"\nrender=()=>{}; showError=(error)=>{globalThis.lastError=error.message};",context);
 return {context,timers,run:code=>vm.runInContext(code,context)};
}
test('chapter identity and chapter view survive restoration',()=>{const h=harness({view:'chapter',selectedLessonId:'lesson_cell_theory',selectedLessonVersion:1});h.run("lessons=[{lesson_id:'lesson_cell_theory',version:1}];restoreDraft();");assert.equal(h.run('state.view'),'chapter');assert.equal(h.run('selectedChapter().lesson_id'),'lesson_cell_theory');});
test('navigation and browser history are allowed while a paid operation is busy',()=>{const h=harness();h.run("busy=true;navigate('lessons');");assert.equal(h.run('state.view'),'lessons');assert.equal(h.context.location.hash,'#lessons');});
test('prepared explanation routes to preparing until quiz is approved',()=>{const h=harness();h.run("remote={status:'teaching',explanation:{text:'Ready to read'},attempts:[]};");assert.equal(h.run('resumeView()'),'preparing');});
test('feedback and reteaching have resumable preparation states',()=>{const h=harness();for(const status of ['reteaching','grading']){h.run(`remote={status:'${status}',attempts:[{feedback:null}]};`);assert.equal(h.run('resumeView()'),'preparing');}h.run("remote={status:'grading',attempts:[{feedback:{summary:'Saved'}}]};");assert.equal(h.run('resumeView()'),'results');});
test('lost start response is recovered by request ID with GET only',async()=>{const h=harness();const calls=[];h.context.capture=path=>{calls.push(path);return {pending:true}};h.run("compatibleApi=true;state.requestId='request_abcd';state.sessionId=null;api=async path=>capture(path);");await h.run('readProgress()');assert.deepEqual(calls,['/api/start-progress?request_id=request_abcd']);});
test('older running backend is detected and never polled through unsupported endpoints',async()=>{const h=harness();h.run("checkConfig({mode:'real_controller_live_agents',token:'not-a-real-token'});state.requestId='request_abcd';");assert.equal(h.run('compatibleApi'),false);assert.equal((await h.run('readProgress()')).pending,true);h.run('watchPreparation()');assert.equal(h.timers.size,0);});
test('pending stage polling works after busy has ended and stops at terminal state',async()=>{const h=harness();h.run("compatibleApi=true;state.requestId='request_abcd';state.view='lessons';busy=false;api=async()=>({session_id:'session_abcdefghijkl',status:'awaiting_answers',attempts:[],lesson:{title:'Biology'},quiz:{quiz_id:'q_test',questions:[]}});watchPreparation();");const callback=[...h.timers.values()][0];h.timers.clear();await callback();assert.equal(h.run('remote.status'),'awaiting_answers');assert.equal(h.timers.size,0);assert.equal(h.run('state.view'),'lessons');});
test('only mutation controls are disabled during preparation',()=>{assert.ok(source.includes('button[data-mutating="true"]'));assert.ok(!source.includes("if(busy)return;state.view=view"));});

test('opening the site starts on landing regardless of an old saved screen',()=>{const h=harness({view:'preparing',sessionId:'session_old'});h.run('restoreDraft()');assert.equal(h.run('initialView(null)'),'library');assert.equal(h.run("initialView('lessons')"),'lessons');});
test('buttons rendered while busy become usable after the operation finishes',()=>{const h=harness();h.run("busy=true;globalThis.control=paidButton('Continue',()=>{});document.querySelectorAll=()=>[control];");assert.equal(h.context.control.disabled,true);h.run('busyState(false)');assert.equal(h.context.control.disabled,false);h.run("control.dataset.unavailable='true';busyState(true);busyState(false)");assert.equal(h.context.control.disabled,true);});
test('terminal polling unlocks controls and a late POST cannot restore a stale preparing snapshot',async()=>{const h=harness();let finish;h.context.pendingPost=new Promise(resolve=>finish=resolve);h.run("compatibleApi=true;state.requestId='request_race';state.view='preparing';api=async()=>({session_id:'session_new',status:'awaiting_answers',attempts:[],lesson:{title:'Biology'},quiz:{quiz_id:'quiz_new',questions:[]}});");const post=h.run("act(()=>pendingPost,'lesson','Starting')");h.run('watchPreparation()');const callback=[...h.timers.values()][0];h.timers.clear();await callback();assert.equal(h.run('busy'),false);finish({session_id:'session_new',status:'teaching',attempts:[],quiz:null});await post;assert.equal(h.run('remote.status'),'awaiting_answers');});
test('a saved preparing URL resolves to failure or summary after restart',()=>{const h=harness();const renderSource=source.slice(source.indexOf('function render(){'),source.indexOf("document.querySelectorAll('.wordmark"));h.run(renderSource+"\ncompatibleApi=true;status=()=>{};summary=()=>{};state.view='preparing';remote={status:'failed',attempts:[]};render();");assert.equal(h.run('state.view'),'status');h.run("state.view='preparing';remote={status:'completed',attempts:[]};render();");assert.equal(h.run('state.view'),'summary');});
test('polling does not cancel a submission or retry while the server still returns its previous state',async()=>{for(const status of ['awaiting_answers','grading']){const h=harness();h.run(`compatibleApi=true;busy=true;state.requestId='request_wait';state.sessionId='session_same';state.view='preparing';remote={session_id:'session_same',status:'${status}',attempts:[{feedback:{summary:'Saved'}}],quiz:null};api=async()=>remote;watchPreparation();`);const callback=[...h.timers.values()][0];h.timers.clear();await callback();assert.equal(h.run('busy'),true);assert.equal(h.timers.size,1);assert.equal(h.run('state.view'),'preparing');}});
test('the chosen reading path survives reload and routes an approved quiz correctly',()=>{const h=harness({view:'preparing',nextView:'quiz'});h.run("restoreDraft();remote={status:'awaiting_answers',quiz:{quiz_id:'quiz_ready'},attempts:[]};");assert.equal(h.run('resumeView()'),'quiz');h.run("state.nextView='lesson';state.view='preparing'");assert.equal(h.run('resumeView()'),'lesson');});

test('Home cancels requests, clears session and answers, and ignores the late action result',async()=>{
 const h=harness();let complete;h.context.lateResponse=new Promise(resolve=>complete=resolve);const cancellations=[];let aborted=0,removed=false;
 h.context.cancel=payload=>{cancellations.push(JSON.parse(JSON.stringify(payload)));return {cancelled:true}};
 h.context.abort=()=>aborted++;h.context.document.getElementById('reset-dialog').open=false;h.context.localStorage.removeItem=()=>removed=true;
 h.run("lessons=[{lesson_id:'lesson_cell_theory',version:1}];state.sessionId='session_old';state.requestId='request_old';state.answers={q_old:'a'};remote={status:'teaching',attempts:[]};pendingRequests.add({abort:()=>abort()});api=async(path,payload)=>cancel(payload);");
 const action=h.run("act(()=>lateResponse,'lesson','Preparing')");await h.run('home()');
 assert.equal(aborted,1);assert.equal(removed,true);assert.equal(h.run('state.view'),'library');assert.equal(h.run('state.sessionId'),null);assert.equal(h.run('state.requestId'),null);assert.equal(h.run('Object.keys(state.answers).length'),0);assert.equal(h.run('remote'),null);assert.equal(h.run('busy'),false);assert.equal(h.context.location.hash,'#library');assert.deepEqual(cancellations,[{session_id:'session_old',request_id:'request_old'}]);
 complete({session_id:'session_old',status:'awaiting_answers',quiz:{quiz_id:'quiz_old',questions:[]},attempts:[]});await action;
 assert.equal(h.run('remote'),null);assert.equal(h.run('state.view'),'library');
});
test('an old reconnect response cannot move the user away from a fresh Home',async()=>{
 const h=harness();let complete;h.context.response=new Promise(resolve=>complete=resolve);h.context.fetch=async()=>({ok:true,json:async()=>({mode:'real_controller_live_agents',token:'test',api_version:2})});h.context.localStorage.removeItem=()=>{};
 h.run("lessons=[{}];state.sessionId='session_old';readProgress=()=>response;api=async()=>({cancelled:true});");const connecting=h.run('reconnect()');await new Promise(resolve=>setImmediate(resolve));await h.run('home()');complete({session_id:'session_old',status:'completed',attempts:[]});await connecting;assert.equal(h.run('remote'),null);assert.equal(h.run('state.view'),'library');
});
test('the new-session dialog appears only after a completed saved summary',()=>{const h=harness();h.run("globalThis.dialogs=0;globalThis.direct=0;$('reset-dialog').querySelector=()=>({textContent:''});$('reset-dialog').showModal=()=>dialogs++;beginNewSession=async()=>{direct++};");for(const status of ['awaiting_answers','failed','pending_save']){h.run(`remote={status:'${status}',attempts:[]};requestNewSession({title:'Next chapter'});`);}assert.equal(h.run('dialogs'),0);assert.equal(h.run('direct'),3);h.run("state.view='summary';remote={status:'completed',summary:{message:'Saved'},attempts:[]};requestNewSession({title:'Next chapter'});");assert.equal(h.run('dialogs'),1);h.run("remote={status:'completed',summary:null,attempts:[]};requestNewSession({title:'Next chapter'});");assert.equal(h.run('dialogs'),1);});
test('switching chapters waits for cancellation and then starts without a dialog',async()=>{const h=harness();let finish;h.context.cancellation=new Promise(resolve=>finish=resolve);h.run("lessons=[{lesson_id:'lesson_cell_theory',version:1}];state.sessionId='session_previous';state.requestId='request_previous';remote={status:'awaiting_answers',attempts:[]};api=()=>cancellation;globalThis.started=0;start=(item,view)=>{started++;globalThis.destination=view;};");const changing=h.run("beginNewSession(lessons[0],'quiz')");assert.equal(h.run('started'),0);assert.equal(h.run('remote'),null);finish({cancelled:true});await changing;assert.equal(h.run('started'),1);assert.equal(h.run('destination'),'quiz');});
test('the previous-session banner cannot survive Home or advertise stopped work',async()=>{const h=harness();h.context.localStorage.removeItem=()=>{};h.run("lessons=[{}];api=async()=>({cancelled:true});state.sessionId='session_previous';remote={session_id:'session_previous',status:'awaiting_answers',attempts:[]};");assert.equal(h.run('hasResumableSession()'),true);await h.run('home()');h.run("navigate('lessons')");assert.equal(h.run('hasResumableSession()'),false);for(const status of ['failed','completed']){h.run(`state.sessionId='session_previous';remote={session_id:'session_previous',status:'${status}'};`);assert.equal(h.run('hasResumableSession()'),false);}h.run("state.sessionId=null;remote={session_id:'session_previous',status:'awaiting_answers'};");assert.equal(h.run('hasResumableSession()'),false);});


test('quiz regeneration is explicit, scoped to review failures, and starts a fresh request once',async()=>{
 const h=harness();let calls=0;h.context.restart=()=>{calls++;return Promise.resolve();};
 h.run("lessons=[{lesson_id:'lesson_cell_theory',version:1}];remote={status:'failed',failure:{code:'review_uncertainty'},lesson:{lesson_id:'lesson_cell_theory',version:1}};beginNewSession=async(item,destination)=>{busy=true;globalThis.restartedLesson=item.lesson_id;globalThis.restartedView=destination;return restart();};");
 assert.equal(calls,0);assert.equal(h.run('canGenerateQuizAgain()'),true);
 await h.run('generateQuizAgain()');await h.run('generateQuizAgain()');
 assert.equal(calls,1);assert.equal(h.context.restartedLesson,'lesson_cell_theory');assert.equal(h.context.restartedView,'lesson');
 h.run("busy=false;remote.failure.code='user_cancelled'");assert.equal(h.run('canGenerateQuizAgain()'),false);
 await h.run('generateQuizAgain()');assert.equal(calls,1);
});


test('direct quiz submission requires all answers and submits once without confirmation',async()=>{
 const h=harness();let calls=0;h.context.send=()=>{calls++;return {status:'grading',attempts:[]};};
 h.run("remote={quiz:{quiz_id:'quiz_one',questions:[{question_id:'q_one'},{question_id:'q_two'}]},attempts:[]};state.sessionId='session_one';state.answers={q_one:'a'};api=async()=>send();");
 await h.run('submitQuiz()');assert.equal(calls,0);assert.equal(h.run('state.index'),1);
 h.run("state.answers.q_two='b';");await h.run('submitQuiz()');assert.equal(calls,1);
 h.run("busy=true");await h.run('submitQuiz()');assert.equal(calls,1);
});
test('legacy confirmation route returns to the final question without sending answers',()=>{
 const h=harness();h.run("state.view='confirm';confirm();");assert.equal(h.run('state.view'),'quiz');assert.equal(h.run('state.index'),9);
});

test('feedback completion routes to results even if the submitted quiz is retained',()=>{
 const h=harness();h.run("state.view='preparing';remote={status:'grading',quiz:{quiz_id:'quiz_submitted'},attempts:[{feedback:{summary:'Ready'}}]};");
 assert.equal(h.run('resumeView()'),'results');
});
test('submission enters loading with busy set before rendering and never flashes lesson',async()=>{
 const h=harness();let finish;h.context.pending=new Promise(resolve=>finish=resolve);
 h.run("remote={status:'awaiting_answers',quiz:{quiz_id:'quiz_one',questions:[{question_id:'q_one'}]},attempts:[]};state.answers={q_one:'a'};state.view='quiz';globalThis.frames=[];render=()=>frames.push({view:state.view,busy});api=()=>pending;");
 const post=h.run('submitQuiz()');assert.equal(h.run('state.view'),'preparing');assert.equal(h.run('frames.at(-1).busy'),true);
 finish({status:'grading',attempts:[{feedback:{summary:'Done'}}],quiz:null});await post;
 assert.equal(h.run('state.view'),'results');assert.equal(h.run("frames.some(f=>f.view==='lesson')"),false);
});

test('completed state cannot show the next-chapter dialog away from final summary',()=>{
 const h=harness();h.run("globalThis.dialogs=0;globalThis.direct=0;$('reset-dialog').showModal=()=>dialogs++;beginNewSession=async()=>{direct++};remote={status:'completed',summary:{message:'Saved'},attempts:[]};");
 for(const view of ['library','lessons','chapter'])h.run(`state.view='${view}';requestNewSession({title:'Next chapter'});`);
 assert.equal(h.run('dialogs'),0);assert.equal(h.run('direct'),3);
});

test('step navigation enables available content and keeps unavailable review disabled',()=>{
 const h=harness();h.run("remote={explanation:{text:'Read'},quiz:{questions:[]},attempts:[]};globalThis.nav=steps(1);");
 assert.equal(h.run('nav.children[0].children[0].disabled'),false);
 assert.equal(h.run('nav.children[2].children[0].disabled'),true);
 h.run('nav.children[0].children[0].onclick()');assert.equal(h.run('state.view'),'lesson');
 h.run("remote.attempts=[{feedback:{summary:'Ready'}}];nav=steps(2);nav.children[2].children[0].onclick();");assert.equal(h.run('state.view'),'results');
});

test('quiz regeneration explains the recorded failure without inventing question details',()=>{
 const h=harness();h.run("remote={failure:{code:'review_uncertainty'}}");assert.match(h.run('quizRegenerationReason()'),/could not confidently verify/);
 h.run("remote.failure.code='content_revision_exhausted'");assert.match(h.run('quizRegenerationReason()'),/after its correction attempt/);
});

test('invalid review response offers deliberate regeneration with an accurate explanation',()=>{
 const h=harness();h.run("remote={status:'failed',failure:{code:'review_invalid_report'}}");
 assert.equal(h.run('canGenerateQuizAgain()'),true);
 assert.match(h.run('quizRegenerationReason()'),/could not read or validate/);
 h.run("remote.failure.code='user_cancelled'");assert.equal(h.run('canGenerateQuizAgain()'),false);
});
