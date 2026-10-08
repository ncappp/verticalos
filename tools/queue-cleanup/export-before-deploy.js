(async()=>{
 const server='https://verticalos-rxdl.onrender.com';
 if(location.origin!==server)throw new Error('Откройте именно FaxClip Mini App на verticalos-rxdl.onrender.com');
 if(typeof api!=='function')throw new Error('Сначала дождитесь загрузки FaxClip');
 const [devices,accounts,tasks,publications,clips]=await Promise.all(['/devices','/accounts','/tasks','/publications','/clips'].map(p=>api(p)));
 const output={format:'FAXCLIP_PREDEPLOY_METADATA_V1',server,created_at:new Date().toISOString(),devices,accounts,tasks,publications,clips};
 const url=URL.createObjectURL(new Blob([JSON.stringify(output)],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download='faxclip-before-deploy.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);
 alert('Сохранено: faxclip-before-deploy.json. Передайте файл в Downloads на Mac. Deploy пока не запускайте.');
})().catch(e=>alert(e.message));
