const open = () => new Promise((resolve,reject) => {
  const request=indexedDB.open('mill-city-field-notebooks',1);
  request.onupgradeneeded=()=>request.result.createObjectStore('drafts',{keyPath:'key'});
  request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
});
export async function cachedNotes(user) {
  const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('drafts');const req=tx.objectStore('drafts').getAll();req.onsuccess=()=>resolve(req.result.filter(x=>x.user===user));req.onerror=()=>reject(req.error);tx.oncomplete=()=>db.close();});
}
export async function cacheNote(user,note,dirty) {
  const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('drafts','readwrite');tx.objectStore('drafts').put({key:`${user}:${note.id}`,user,note,dirty});tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>{db.close();reject(tx.error);};});
}
