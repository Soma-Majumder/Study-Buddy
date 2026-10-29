const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('demo/student/student.js','utf8');
const startCode=source.slice(source.indexOf('function start('),source.indexOf('function library()'));
function harness(){let sequence=0;const sent=[];const context={busy:false,lessons:[],liveMode:true,paidEnabled:true,state:{},crypto:{randomUUID:()=>`request_${++sequence}`},save(){},selectChapter(){},navigate(){},watchPreparation(){},showError(){throw Error('Unexpected error');},api:(path,body)=>sent.push({path,body}),act:task=>task()};vm.createContext(context);vm.runInContext(startCode,context);return {context,sent};}
test('all chapters have distinct start identities and stable replays',()=>{const {context,sent}=harness();const lessons=fs.readdirSync('lessons').filter(n=>n.endsWith('.json')).map(n=>JSON.parse(fs.readFileSync('lessons/'+n))).filter(l=>l.approved);assert.equal(lessons.length,4);const ids=new Set();for(const lesson of lessons){context.start(lesson);context.start(lesson);const [first,replay]=sent.slice(-2);assert.equal(first.body.lesson_id,lesson.lesson_id);assert.equal(first.body.lesson_version,lesson.version);assert.equal(first.body.request_id,replay.body.request_id);assert.ok(!ids.has(first.body.request_id));ids.add(first.body.request_id);}});
test('legacy draft IDs are not reused without a matching chapter',()=>{const {context,sent}=harness();context.state.requestId='legacy_request';context.start({lesson_id:'lesson_active_transport',version:1});assert.notEqual(sent[0].body.request_id,'legacy_request');});
test('busy clicks do not create another request',()=>{const {context,sent}=harness();context.busy=true;context.start({lesson_id:'lesson_cell_theory',version:1});assert.equal(sent.length,0);});
