const $ = (id) => document.getElementById(id);
const activeStates = new Set(['queued','downloading','probing','transcribing','ranking','rendering']);
let selected = null, projects = [], clipSignature = '', sourceSignature = '', librarySignature = '', sourceKind = 'file', objectUrl = null;
let youtubeLoginContext = null, youtubeLoginChoice = null, youtubeDialogEpoch = 0;
const stateNames = {queued:'Mengantre',downloading:'Mengunduh',probing:'Memeriksa',transcribing:'Transkripsi',ranking:'Kurasi',rendering:'Render',complete:'Selesai',error:'Perlu diperiksa',cancelled:'Dibatalkan',interrupted:'Terhenti'};
const time = (seconds) => `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;
function node(tag, className, text) { const element = document.createElement(tag); if (className) element.className=className; if (text!==undefined) element.textContent=text; return element; }
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('toast').hidden=true,4500); }
async function api(path, options={}) { const response=await fetch(path,options); if(!response.ok) { let message=`Permintaan gagal (${response.status}).`; try {const data=await response.json();message=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail);}catch{} throw new Error(message); }return response.json(); }
function urlFor(project,clip,kind,download=false) { return `/api/projects/${project.id}/clips/${clip.id}/${kind}?v=${clip.artifact?.version||''}${download?'&download=true':''}`; }
function isYoutubeUrl(value) {
  try { const host=new URL(value).hostname.toLowerCase().replace(/\.$/,'');return ['youtube.com','youtu.be','youtube-nocookie.com'].some(domain=>host===domain||host.endsWith(`.${domain}`)); } catch { return false; }
}
function showYoutubeCookieField(prefix='') {
  const choice=$(`${prefix}youtube-auth`), input=$(`${prefix}youtube-cookies`), file=choice.value==='file';
  $(`${prefix}youtube-cookie-row`).hidden=!file;input.disabled=!file||choice.disabled||(prefix==='new-'&&sourceKind!=='url');input.required=!input.disabled&&prefix==='new-';
  $(`${prefix}youtube-session-info`).textContent=$(`${prefix}youtube-profile`).value==='openclip'?'Sesi dipakai dari jendela login khusus OpenClip.':'';
}
function renderYoutubeAccess(project,busy) {
  const details=$('youtube-access'), form=$('youtube-auth-form');details.hidden=!isYoutubeUrl(project.url);
  if(details.hidden)return;
  if(form.dataset.project!==project.id){
    form.reset();form.dataset.project=project.id;$('youtube-auth').value=project.youtube_auth||'none';$('youtube-profile').value=project.youtube_profile||'default';
    $('youtube-auth-message').textContent='';$('youtube-auth-error').hidden=true;
    details.open=project.status==='error'&&/youtube|cookie|not a bot/i.test(project.error||'');
  }
  for(const field of form.querySelectorAll('input,select,button'))field.disabled=busy||form.dataset.saving==='true';
  showYoutubeCookieField();
}
function reset() { $('existing-player').pause();selected=null;clipSignature='';sourceSignature='';$('create-form').hidden=false;$('existing-source').hidden=true;$('delete-project').hidden=true;$('page-title').textContent='Video panjang. Potongan pilihan.';$('page-description').textContent='Pilih momen, rapikan format, simpan klip.';$('clips').replaceChildren();$('job-status').hidden=true;$('job-error').hidden=true;$('job-warnings').hidden=true;$('youtube-error-actions').hidden=true;$('clip-total').textContent='';$('empty-state').hidden=false;$('export-tools').hidden=true;$('ranking-label').textContent='SIAP DIPROSES';history.replaceState(null,'','/');renderLibrary(); }
function choose(project) {$('source-player').pause();selected=project.id;clipSignature='';sourceSignature='';$('export-location').textContent='';$('open-exports').hidden=true;$('create-form').hidden=true;$('existing-source').hidden=false;$('delete-project').hidden=false;$('transcript-content').hidden=true;history.replaceState(null,'',`/?project=${project.id}`);renderProject(project);renderLibrary();}
function renderLibrary() { const signature=JSON.stringify(projects.map(p=>[p.id,p.name,p.status,selected]));if(signature===librarySignature)return;librarySignature=signature;$('project-count').textContent=projects.length;const fragment=document.createDocumentFragment();for(const project of projects){const button=node('button',`project-item${project.id===selected?' selected':''}`);button.append(node('strong','',project.name),node('small','',`${stateNames[project.status]||project.status} · ${new Date(project.created_at).toLocaleDateString('id-ID',{day:'numeric',month:'short'})}`));button.addEventListener('click',()=>choose(project));fragment.append(button);}if(!projects.length)fragment.append(node('p','muted small','Belum ada proyek.'));$('projects').replaceChildren(fragment);}
function renderProject(project) {
  const busy=activeStates.has(project.status);
  $('page-title').textContent=project.name;$('page-description').textContent=`${stateNames[project.status]||project.status}${project.metadata?` · ${time(project.metadata.duration)} sumber · ${project.language||project.config.language}`:''}`;
  $('job-status').hidden=false;$('job-message').textContent=project.message;$('job-percent').textContent=`${project.progress}%`;$('progress').value=project.progress;
  $('cancel-project').hidden=!busy;$('resume-project').hidden=!['error','cancelled','interrupted'].includes(project.status);$('delete-project').disabled=busy;
  const stages=[['probing','Sumber'],['transcribing','Transkrip'],['ranking','Kurasi'],['rendering','Render'],['complete','Siap']];let index=stages.findIndex(x=>x[0]===project.status);if(project.status==='downloading'||project.status==='queued')index=0;
  $('stage-list').replaceChildren(...stages.map(([id,label],i)=>node('span',i<index?'done':i===index?'current':'',`${i+1}. ${label}`)));
  $('job-error').hidden=!project.error;$('job-error').textContent=project.error||'';$('job-warnings').hidden=!project.warnings.length;$('job-warnings').replaceChildren(...project.warnings.map(w=>node('p','',w)));
  $('youtube-error-actions').hidden=!project.error||!isYoutubeUrl(project.url);
  const sourceKey=`${project.id}:${project.source_file||''}`;if(project.source_file && sourceSignature!==sourceKey){$('existing-player').src=`/api/projects/${project.id}/source?v=${encodeURIComponent(project.source_file)}`;sourceSignature=sourceKey;}
  if(!project.source_file&&$('existing-player').hasAttribute('src')){$('existing-player').pause();$('existing-player').removeAttribute('src');$('existing-player').load();sourceSignature='';}
  $('existing-meta').textContent=project.metadata?`${project.metadata.width} × ${project.metadata.height} · ${time(project.metadata.duration)}`:'Sumber sedang disiapkan.';
  const config=project.config;$('existing-settings').replaceChildren(node('p','',`${config.aspect} · ${config.min_duration}–${config.max_duration} detik · ${config.clip_count} klip diminta`),node('p','',`Subtitle ${config.captions?'aktif':'nonaktif'} · Pemrosesan subtitle dan kurasi: OpenRouter`));
  renderYoutubeAccess(project,busy);
  if(project.openrouter_cost)$('existing-settings').append(node('p','',`Biaya tercatat: $${project.openrouter_cost.toFixed(4)}`));
  $('quality-form').querySelector('button').disabled=busy;
  $('reframe-project').disabled=busy||!project.clips.length||!project.transcript_timing;
  const imageForm=$('image-quality-form');imageForm.querySelector('button').disabled=busy||!project.clips.length||!project.transcript_timing;
  if(imageForm.dataset.project!==project.id){imageForm.dataset.project=project.id;$('upgrade-source').checked=Boolean(project.url);$('upgrade-resolution').value='2160';$('image-quality-error').hidden=true;}
  $('upgrade-source').disabled=busy||!project.url;$('upgrade-resolution').disabled=busy||!project.url;
  $('image-quality-hint').textContent=(project.metadata?`Sumber tersimpan ${project.metadata.width} × ${project.metadata.height}. `:'')+(project.url?'Ambil sumber lebih tinggi untuk menjaga detail saat crop vertikal.':'Proyek ini memakai file unggahan. Untuk menambah detail sumber, gunakan file asli beresolusi lebih tinggi pada proyek baru.');
  if(config.prompt)$('existing-settings').append(node('p','',`Topik: ${config.prompt}`));
  const hasTranscript=Boolean(project.transcript_timing);$('transcript-download').hidden=!hasTranscript;$('show-transcript').hidden=!hasTranscript;$('transcript-download').href=`/api/projects/${project.id}/transcript.srt`;
  const ready=project.clips.filter(c=>c.artifact).length;$('clip-total').textContent=project.clips.length?`(${ready}/${project.clips.length})`:'';$('ranking-label').textContent=project.clips.length&&!project.clips.some(c=>c.ranker==='openrouter')?'HASIL SEBELUMNYA':'PENILAIAN OPENROUTER';
  $('export-tools').hidden=!ready;$('download-all').href=`/api/projects/${project.id}/download.zip`;
  $('empty-state').hidden=Boolean(project.clips.length)||busy;
  const signature=JSON.stringify([project.id,project.clips,busy]);if(signature!==clipSignature){clipSignature=signature;$('clips').replaceChildren(...project.clips.map(clip=>clipCard(project,clip,busy)));}
}
function clipCard(project,clip,busy){
  const article=node('article',`clip-card ${project.config.aspect==='16:9'?'landscape':project.config.aspect==='1:1'?'square':''}`);
  if(clip.artifact){const wrap=node('div','clip-video-wrap');const video=node('video');video.controls=true;video.preload='none';video.playsInline=true;video.poster=urlFor(project,clip,'poster');video.src=urlFor(project,clip,'video');video.setAttribute('aria-label',clip.title);wrap.append(video);article.append(wrap);}else article.append(node('div','clip-pending',clip.status==='rendering'?'Merender video…':'Menunggu render'));
  const meta=node('div','clip-meta'),score=node('div','clip-score');score.title='Nilai editorial mesin, bukan peluang viral.';score.append(node('strong','',clip.score),node('span','',clip.ranker==='openrouter'?'pilihan OpenRouter':'hasil sebelumnya'));meta.append(score,node('span','clip-time',`${time(clip.start)}–${time(clip.end)}`));article.append(meta,node('h3','',clip.title),node('p','clip-reason',clip.reason));
  if(clip.artifact){
    const actions=node('div','clip-actions');for(const [kind,label]of [['video','Unduh MP4'],['srt','SRT'],['timeline','Timeline']]){const a=node('a','download',label);a.href=urlFor(project,clip,kind,true);a.setAttribute('download','');a.addEventListener('click',()=>toast('Unduhan diminta. Periksa Downloads, atau gunakan Simpan hasil ke folder.'));actions.append(a);}article.append(actions);
    const details=node('details','clip-edit'),summary=node('summary','','Atur potongan dan posisi');details.append(summary);const form=node('form');
    function field(label,type,value,name){const id=`${clip.id}-${name}`;const container=node('div');const l=node('label','',label);l.htmlFor=id;const input=node('input');input.type=type;input.value=value;input.id=id;input.name=name;input.required=true;if(type==='number'){input.min='0';input.step='.1';}container.append(l,input);return container;}
    form.append(field('Judul','text',clip.title,'title'));form.querySelector('[name=title]').maxLength=100;const row=node('div','field-row');row.append(field('Mulai (detik)','number',clip.start,'start'),field('Selesai (detik)','number',clip.end,'end'));form.append(row);
    const cameraLabel=node('label','','Kamera');cameraLabel.htmlFor=`${clip.id}-camera`;const camera=node('select');camera.id=cameraLabel.htmlFor;camera.name='camera_mode';for(const [value,label]of [['auto','Ikuti pembicara (lokal)'],['manual','Posisi manual'],['fit','Tampilkan seluruh gambar']]){const option=node('option','',label);option.value=value;camera.append(option);}camera.value=clip.camera_mode||(clip.manual_focal?'manual':project.config.framing==='center'?'manual':project.config.framing);camera.disabled=busy;form.append(cameraLabel,camera);
    const focal=field('Posisi crop: kiri ke kanan','range',clip.focal??.5,'focal');const range=focal.querySelector('input');range.min='0';range.max='1';range.step='.01';range.disabled=busy||camera.value!=='manual';camera.addEventListener('change',()=>{range.disabled=busy||camera.value!=='manual';});form.append(focal,node('p','helper','Mode pembicara mengikuti gerakan bibir dan menahan kamera saat jeda. Pilih Posisi manual untuk mengatur crop sendiri.'));
    const button=node('button','secondary','Render ulang');button.type='submit';button.disabled=busy;form.append(button);form.addEventListener('submit',async event=>{event.preventDefault();const values=new FormData(form);button.disabled=true;try{const body={title:values.get('title'),start:Number(values.get('start')),end:Number(values.get('end')),focal:Number(values.get('focal')??clip.focal??.5),camera_mode:values.get('camera_mode')};await api(`/api/projects/${project.id}/clips/${clip.id}/render`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('Render ulang masuk antrean.');await refresh();}catch(error){toast(error.message);button.disabled=false;}});details.append(form);article.append(details);
  }
  return article;
}
async function refresh(){try{projects=await api('/api/projects');$('connection').textContent='Terhubung ke laptop';$('connection').classList.remove('offline');renderLibrary();if(selected){const project=projects.find(p=>p.id===selected);if(project)renderProject(project);else reset();}}catch(error){$('connection').textContent='Server tidak terhubung';$('connection').classList.add('offline');}}
$('new-project').addEventListener('click',reset);
for(const tab of document.querySelectorAll('.source-tab'))tab.addEventListener('click',()=>{sourceKind=tab.dataset.source;for(const t of document.querySelectorAll('.source-tab')){t.classList.toggle('active',t===tab);t.setAttribute('aria-pressed',String(t===tab));}$('file-source').hidden=sourceKind!=='file';$('url-source').hidden=sourceKind!=='url';$('source-preview').hidden=sourceKind!=='file'||!$('video-input').files.length;showYoutubeCookieField('new-');});
function previewFile(){const file=$('video-input').files[0];if(!file)return;if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=URL.createObjectURL(file);$('source-player').src=objectUrl;$('source-preview').hidden=false;$('upload-label').textContent=file.name;$('source-meta').textContent=`${(file.size/1024**2).toFixed(1)} MB`;}
$('video-input').addEventListener('change',previewFile);const drop=$('drop-zone');for(const event of ['dragenter','dragover'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('dragging');});for(const event of ['dragleave','drop'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('dragging');});drop.addEventListener('drop',e=>{if(e.dataTransfer.files.length){const transfer=new DataTransfer();transfer.items.add(e.dataTransfer.files[0]);$('video-input').files=transfer.files;previewFile();}});
$('create-form').addEventListener('submit',async event=>{
  event.preventDefault();const errorBox=$('form-error');errorBox.hidden=true;const data=new FormData(event.target);
  if(Number(data.get('min_duration'))>Number(data.get('max_duration'))){errorBox.textContent='Durasi minimum tidak boleh lebih besar dari maksimum.';errorBox.hidden=false;return;}
  if(sourceKind==='file'){
    for(const key of ['url','youtube_auth','youtube_cookies','youtube_profile'])data.delete(key);
    if(!$('video-input').files.length){errorBox.textContent='Pilih file video terlebih dahulu.';errorBox.hidden=false;return;}
  }else{
    data.delete('video');if(!$('url-input').value.trim()){errorBox.textContent='Masukkan tautan video terlebih dahulu.';errorBox.hidden=false;return;}
  }
  data.set('captions',String($('captions').checked));const button=$('submit-button');button.disabled=true;button.textContent='Mengirim sumber…';
  try{
    const project=await api('/api/projects',{method:'POST',body:data});projects.unshift(project);choose(project);event.target.reset();showYoutubeCookieField('new-');
    $('upload-label').textContent='Pilih atau letakkan video';$('source-preview').hidden=true;toast('Proyek masuk antrean lokal.');
  }catch(error){errorBox.textContent=error.message;errorBox.hidden=false;}
  finally{button.disabled=false;button.replaceChildren(node('span','','Buat klip'));}
});
for(const prefix of ['','new-'])$(`${prefix}youtube-auth`).addEventListener('change',()=>{$(`${prefix}youtube-profile`).value='default';showYoutubeCookieField(prefix);});
$('youtube-auth-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target, jobId=selected, data=new FormData(form);form.dataset.saving='true';$('youtube-auth-error').hidden=true;
  for(const field of form.querySelectorAll('input,select,button'))field.disabled=true;
  try{
    const project=await api(`/api/projects/${jobId}/youtube-auth`,{method:'POST',body:data});projects=projects.map(p=>p.id===jobId?project:p);
    if(selected===jobId){form.dataset.project='';renderProject(project);$('youtube-auth-message').textContent=project.youtube_auth==='none'?'Akses disimpan tanpa sesi login. File cookie tersimpan dihapus.':['error','cancelled','interrupted'].includes(project.status)?'Akses disimpan. Klik Lanjutkan / coba lagi untuk menjalankan unduhan dengan sesi ini.':'Akses disimpan. Sesi ini akan dipakai saat mengunduh ulang sumber.';}
    toast('Akses YouTube disimpan.');
  }catch(error){if(selected===jobId){$('youtube-auth-error').textContent=error.message;$('youtube-auth-error').hidden=false;}}
  finally{form.dataset.saving='false';const current=projects.find(p=>p.id===selected);if(current)renderYoutubeAccess(current,activeStates.has(current.status));}
});
$('cancel-project').addEventListener('click',async()=>{try{await api(`/api/projects/${selected}/cancel`,{method:'POST'});await refresh();}catch(error){toast(error.message);}});
$('reframe-project').addEventListener('click',async()=>{const button=$('reframe-project');button.disabled=true;try{await api(`/api/projects/${selected}/reframe`,{method:'POST'});toast('Render kamera pembicara masuk antrean. Transkrip dan potongan tetap dipakai.');await refresh();}catch(error){toast(error.message);button.disabled=false;}});
$('image-quality-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;$('image-quality-error').hidden=true;try{await api(`/api/projects/${selected}/export-quality`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resolution:1080,source_resolution:Number($('upgrade-resolution').value),upgrade_source:$('upgrade-source').checked&&!$('upgrade-source').disabled})});toast('Render kualitas tinggi masuk antrean. Subtitle dan potongan dipakai kembali.');await refresh();}catch(error){$('image-quality-error').textContent=error.message;$('image-quality-error').hidden=false;button.disabled=false;}});
$('resume-project').addEventListener('click',async()=>{try{await api(`/api/projects/${selected}/resume`,{method:'POST'});await refresh();}catch(error){toast(error.message);}});
$('delete-project').addEventListener('click',async()=>{if(!confirm('Hapus sumber dan semua hasil proyek ini dari laptop?'))return;try{await api(`/api/projects/${selected}`,{method:'DELETE'});reset();await refresh();}catch(error){toast(error.message);}});
$('show-transcript').addEventListener('click',async()=>{const content=$('transcript-content');if(!content.hidden){content.hidden=true;$('show-transcript').textContent='Lihat transkrip';return;}try{const data=await api(`/api/projects/${selected}/transcript`);content.replaceChildren(...data.segments.map(segment=>{const b=node('button','transcript-line');b.append(node('small','',time(segment.start)),node('span','',segment.text));b.addEventListener('click',()=>{$('existing-player').currentTime=segment.start;$('existing-player').play().catch(()=>{});});return b;}));content.hidden=false;$('show-transcript').textContent='Tutup transkrip';}catch(error){toast(error.message);}});
async function initialize(){await refresh();const id=new URLSearchParams(location.search).get('project');if(id){const project=projects.find(p=>p.id===id);if(project)choose(project);}try{const health=await api('/api/health');$('machine-info').textContent=`${health.free_gb} GB kosong · 1 job`;}catch{$('machine-info').textContent='Status mesin belum tersedia.';}setInterval(refresh,2000);}
initialize();

$('ai-settings').addEventListener('click',async()=>{const dialog=$('settings-dialog');dialog.showModal();try{const config=await api('/api/settings/openrouter');$('key-status').textContent=config.key_set?'API key sudah tersimpan.':'API key belum diatur.';$('asr-cloud-model').value=config.asr_model;$('chat-cloud-model').value=config.chat_model;}catch(error){$('settings-message').textContent=error.message;}});
$('close-settings').addEventListener('click',()=>$('settings-dialog').close());
$('router-settings-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{const config=await api('/api/settings/openrouter',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:$('api-key').value,asr_model:$('asr-cloud-model').value,chat_model:$('chat-cloud-model').value})});$('api-key').value='';$('key-status').textContent=config.key_set?'API key sudah tersimpan.':'API key belum diatur.';$('settings-message').textContent='Pengaturan disimpan. Subtitle dan kurasi otomatis memakai OpenRouter.';}catch(error){$('settings-message').textContent=error.message;}finally{button.disabled=false;}});
$('quality-form').addEventListener('submit',async event=>{event.preventDefault();const data=new FormData(event.target);const button=event.target.querySelector('button');button.disabled=true;$('quality-error').hidden=true;try{await api(`/api/projects/${selected}/quality`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({min_duration:Number(data.get('min_duration')),max_duration:Number(data.get('max_duration')),resolution:1080})});toast('Pemrosesan OpenRouter dimulai dari sumber tersimpan.');await refresh();}catch(error){$('quality-error').textContent=error.message;$('quality-error').hidden=false;button.disabled=false;}});
$('save-exports').addEventListener('click',async()=>{const button=$('save-exports');button.disabled=true;try{const result=await api(`/api/projects/${selected}/save-exports`,{method:'POST'});$('export-location').textContent=result.path;$('open-exports').hidden=false;toast(`${result.clips} klip disalin ke folder hasil.`);}catch(error){toast(error.message);}finally{button.disabled=false;}});
$('open-exports').addEventListener('click',async()=>{try{const result=await api(`/api/projects/${selected}/open-exports`,{method:'POST'});$('export-location').textContent=result.path;}catch(error){toast(error.message);}});

async function openYoutubeLogin(){
  const dialog=$('youtube-dialog'), epoch=++youtubeDialogEpoch;
  const project=projects.find(p=>p.id===selected);youtubeLoginContext=project&&isYoutubeUrl(project.url)?project.id:null;youtubeLoginChoice=null;
  $('youtube-login-context').textContent=youtubeLoginContext?`Sesi untuk: ${project.name}`:'Siapkan sesi untuk proyek dengan tautan YouTube.';
  $('youtube-login-message').textContent='Memeriksa browser yang tersedia…';$('youtube-login-error').hidden=true;
  $('youtube-login-browser').replaceChildren();$('youtube-login-browser').disabled=false;$('launch-youtube-login').disabled=true;$('use-youtube-login').disabled=true;
  $('use-youtube-login').textContent=youtubeLoginContext&&['error','cancelled','interrupted'].includes(project.status)?'2. Sudah login · coba lagi':'2. Sudah login · gunakan sesi';
  if(!dialog.open)dialog.showModal();
  try{
    const data=await api('/api/youtube/browsers');if(epoch!==youtubeDialogEpoch||!dialog.open)return;
    for(const browser of data.browsers){const option=node('option','',browser.name);option.value=browser.id;$('youtube-login-browser').append(option);}
    if(!data.browsers.length)throw new Error('Chrome/Chromium belum ditemukan. Pasang salah satunya atau gunakan file cookies.txt.');
    if(data.browsers.some(b=>b.id===project?.youtube_auth))$('youtube-login-browser').value=project.youtube_auth;
    $('launch-youtube-login').disabled=false;$('youtube-login-message').textContent='Pilih browser, lalu buka login. Belum ada pemeriksaan akun atau cookie.';
  }catch(error){if(epoch===youtubeDialogEpoch&&dialog.open){$('youtube-login-error').textContent=error.message;$('youtube-login-error').hidden=false;$('youtube-login-message').textContent='';}}
}
for(const id of ['youtube-settings','new-youtube-login','project-youtube-login','error-youtube-login'])$(id).addEventListener('click',openYoutubeLogin);
$('close-youtube').addEventListener('click',()=>$('youtube-dialog').close());
$('youtube-login-browser').addEventListener('change',()=>{youtubeLoginChoice=null;$('use-youtube-login').disabled=true;$('youtube-login-message').textContent='Buka login pada browser yang dipilih sebelum memakai sesinya.';});
$('launch-youtube-login').addEventListener('click',async()=>{
  const button=$('launch-youtube-login'), epoch=youtubeDialogEpoch;button.disabled=true;$('youtube-login-browser').disabled=true;$('youtube-login-error').hidden=true;
  try{
    const choice=await api('/api/youtube/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({browser:$('youtube-login-browser').value})});
    if(epoch!==youtubeDialogEpoch||!$('youtube-dialog').open)return;
    youtubeLoginChoice=choice;$('use-youtube-login').disabled=false;
    $('youtube-login-message').textContent='Perintah membuka YouTube dikirim. Selesaikan login di jendela browser tersebut, pastikan video bisa diputar, lalu tutup jendelanya dan klik tombol langkah 2.';
  }catch(error){if(epoch===youtubeDialogEpoch){$('youtube-login-error').textContent=error.message;$('youtube-login-error').hidden=false;}}
  finally{if(epoch===youtubeDialogEpoch){button.disabled=false;$('youtube-login-browser').disabled=false;}}
});
$('use-youtube-login').addEventListener('click',async()=>{
  if(!youtubeLoginChoice)return;const choice={...youtubeLoginChoice}, jobId=youtubeLoginContext, epoch=youtubeDialogEpoch, button=$('use-youtube-login');button.disabled=true;$('launch-youtube-login').disabled=true;$('youtube-login-error').hidden=true;
  try{
    if(jobId){
      const data=new FormData();data.set('youtube_auth',choice.browser);data.set('youtube_profile',choice.profile);
      let project=await api(`/api/projects/${jobId}/youtube-auth`,{method:'POST',body:data});
      if(['error','cancelled','interrupted'].includes(project.status))project=await api(`/api/projects/${jobId}/resume`,{method:'POST'});
      projects=projects.map(p=>p.id===jobId?project:p);if(selected===jobId){$('youtube-auth-form').dataset.project='';renderProject(project);}renderLibrary();
      toast(activeStates.has(project.status)?'Unduhan masuk antrean dengan sesi login OpenClip.':'Sesi login disimpan untuk unduhan sumber berikutnya.');
    }else{
      if(selected)reset();document.querySelector('[data-source="url"]').click();$('new-youtube-auth').value=choice.browser;$('new-youtube-profile').value=choice.profile;showYoutubeCookieField('new-');$('new-youtube-access').open=true;
      toast('Sesi disiapkan. Masukkan tautan sumber lalu pilih Buat klip.');
    }
    if(epoch===youtubeDialogEpoch)$('youtube-dialog').close();
  }catch(error){if(epoch===youtubeDialogEpoch){$('youtube-login-error').textContent=error.message;$('youtube-login-error').hidden=false;}}
  finally{if(epoch===youtubeDialogEpoch){button.disabled=false;$('launch-youtube-login').disabled=false;}}
});
function openYoutubeCookieSettings(){
  $('youtube-dialog').close();const project=projects.find(p=>p.id===selected), existing=project&&isYoutubeUrl(project.url), prefix=existing?'':'new-';
  if(!existing){if(selected)reset();document.querySelector('[data-source="url"]').click();}
  const details=$(existing?'youtube-access':'new-youtube-access');details.hidden=false;details.open=true;
  $(`${prefix}youtube-auth`).value='file';$(`${prefix}youtube-profile`).value='default';showYoutubeCookieField(prefix);
  details.scrollIntoView({block:'center'});$(`${prefix}youtube-cookies`).focus();
}
$('error-youtube-cookies').addEventListener('click',openYoutubeCookieSettings);
$('youtube-upload-instead').addEventListener('click',openYoutubeCookieSettings);
