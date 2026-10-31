const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const context=vm.createContext({document:{addEventListener(){}}});
vm.runInContext(source.split('\n')[1]+'\n'+source.slice(source.indexOf('const activityDisclosures='),source.indexOf('function renderStatus()')),context);
const render=(details,open=false)=>context.activityMarkup(details,open);
test('provider text is escaped and invalid progress cannot inject markup',()=>{
 const html=render({heading:'<script>',items:[{title:'<img src=x onerror=alert(1)>',progress:'1; color:red',subtitle:'<b>author</b>'}]});
 assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('role="progressbar"'));
});
test('long activity lists disclose after three rows and preserve expanded state',()=>{
 const details={heading:'Open sessions',items:Array.from({length:6},(_,i)=>({title:'Book '+i,progress:25,remaining_minutes:317}))};
 const html=render(details,true);
 assert.ok(html.indexOf('Book 2')<html.indexOf('<details'));
 assert.ok(html.indexOf('Book 3')>html.indexOf('<details'));
 assert.ok(html.includes('More sessions (3)'));assert.match(html,/<details class="activity-more" open>/);
 assert.ok(html.includes('5h 17m remaining'));
});
test('empty and zero-progress states remain distinct',()=>{
 assert.ok(render({heading:'Last listened',items:[],empty:'No history'}).includes('No history'));
 assert.ok(render({heading:'Open sessions',items:[{title:'New book',progress:0}]}).includes('aria-valuenow="0"'));
 assert.equal(render({}), '');
});
