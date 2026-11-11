import {cabinetOptions,serviceOptions,measurementSelection} from './measurement-model.mjs';
export default function MeasurementDetails({value,onChange,disabled}) {
 const selection=measurementSelection(value),custom=selection&&!cabinetOptions.includes(selection);
 const update=(key,v)=>onChange({...value,[key]:v});
 return <div className="ws-measurement-simple">
  <label>Door style & color<select aria-label="Door style & color" disabled={disabled} value={custom?'__other':selection} onChange={e=>update('cabinet_selection',e.target.value==='__other'?'Other: ':e.target.value)}><option value="">Select Highland style & color</option>{cabinetOptions.map(o=><option key={o}>{o}</option>)}<option value="__other">Other</option></select>{custom&&<input aria-label="Other door style & color" disabled={disabled} maxLength={200} value={selection} onChange={e=>update('cabinet_selection',e.target.value||'Other: ')} placeholder="Type or dictate the style and color"/>}</label>
  <label>Ceiling height<input disabled={disabled} maxLength={200} value={value.ceiling_height} onChange={e=>update('ceiling_height',e.target.value)} placeholder="e.g. 96 inches"/></label>
  <label>Service package<select aria-label="Service package" disabled={disabled} value={value.service_package||''} onChange={e=>update('service_package',e.target.value)}><option value="">Select service</option>{serviceOptions.map(o=><option key={o}>{o}</option>)}</select>{value.service_package==='Customer pickup'&&<small>Unassembled cabinets. Customer picks up directly from the warehouse; no delivery or installation.</small>}</label>
 </div>;
}
