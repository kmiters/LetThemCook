import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReferenceLine, Tooltip} from 'recharts';
import {EvilComposedChart as Chart} from './evilcharts/registry/charts/recharts-composed-chart';

const config = {tokens:{label:'Tokeny'}, usd:{label:'Koszt API Standard'}};
const compact = new Intl.NumberFormat('pl-PL',{notation:'compact',maximumSignificantDigits:3});
const ticks = {fontSize:11,fill:'var(--muted)'};

function DayTip({row}) {
  if (!row) return null;
  return <div className="day-tip" role="tooltip">
    <strong><span className="value">{row.dateLabel}</span>{row.today ? ' · dziś trwa' : ''}</strong>
    <div><span className="token-key">{row.tokenLabel}</span><span className={row.tokens === null ? undefined : "value"}>{row.tokenText}</span></div>
    <div><span className="cost-key">Koszt API</span><span className={row.usd === null ? undefined : "value"}>{row.costText}</span></div>
    {row.coverage && <small>Pokrycie wyceny · <span className="value">{row.coverage}</span></small>}
  </div>;
}

function HistoryChart({rows, selected, selectDay}) {
  const [keyboardTip, setKeyboardTip] = useState(false);
  const index = rows.findIndex(r=>r.day===selected);
  const selectIndex = next => selectDay(rows[Math.max(0,Math.min(next,rows.length-1))].day);
  function onKeyDown(event) {
    const next = {ArrowLeft:index-1, ArrowRight:index+1, Home:0, End:rows.length-1}[event.key];
    if (next !== undefined) {
      event.preventDefault();
      setKeyboardTip(true);
      selectIndex(next);
    } else if (event.key === 'Escape') {
      setKeyboardTip(false);
    }
  }
  const dateTick = day => rows.find(r=>r.day===day)?.tickLabel;
  function priceDot({cx, cy, index, payload}) {
    if (!Number.isFinite(cy) || payload.usd === null) return null;
    const isolated = rows[index-1]?.usd == null && rows[index+1]?.usd == null;
    if (rows.length > 30 && payload.day !== selected && !isolated) return null;
    return <circle key={payload.day} cx={cx} cy={cy} r={3}
      fill="var(--paper)" stroke="var(--cost)" strokeWidth={2}/>;
  }
  const onClick = state => {
    if (state.activeLabel && rows.some(r=>r.day===state.activeLabel)) selectDay(state.activeLabel);
  };
  return <div className="chart-frame" tabIndex={0} role="group"
    aria-label="Dzienne tokeny i koszt w USD. Strzałki w lewo i w prawo wybierają dzień."
    onKeyDown={onKeyDown} onFocus={e=>setKeyboardTip(e.target.matches(':focus-visible'))}
    onBlur={()=>setKeyboardTip(false)} onPointerMove={()=>setKeyboardTip(false)}>
    <Chart data={rows} config={config} xDataKey="day" animationType="none" barCategoryGap="24%"
      className="history-chart" chartProps={{accessibilityLayer:false, onClick,
        margin:{top:12,right:0,bottom:2,left:0}}}>
      <Chart.Grid stroke="var(--rule)" strokeDasharray="0" yAxisId="tokens"/>
      <Chart.XAxis dataKey="day" tick={ticks} tickFormatter={dateTick} minTickGap={16} height={28}/>
      <Chart.YAxis yAxisId="tokens" width={62} domain={[0,rows.some(r=>r.tokens>0) ? 'auto' : 1]} tick={ticks} tickCount={4} tickFormatter={n=>compact.format(n)}/>
      <Chart.YAxis yAxisId="usd" orientation="right" width={52} domain={[0,rows.some(r=>r.usd>0) ? 'auto' : 1]} tick={ticks} tickCount={4}
        tickFormatter={n=>'$'+compact.format(n)}/>
      <ReferenceLine x={selected} yAxisId="tokens" stroke="var(--muted)" strokeDasharray="3 4"/>
      <Chart.Bar dataKey="tokens" variant="default" radius={2} barProps={{yAxisId:'tokens',maxBarSize:68}}/>
      <Chart.Line dataKey="usd" curveType="linear" connectNulls={false}
        lineProps={{yAxisId:'usd',stroke:'var(--cost)',strokeWidth:2,
          dot:priceDot, activeDot:false}}/>
      <Tooltip filterNull={false} isAnimationActive={false} cursor={false} shared
        content={({active,label})=>active && !keyboardTip ? <DayTip row={rows.find(r=>r.day===label)}/> : null}/>
    </Chart>
    {keyboardTip && <div className="keyboard-tip" aria-live="polite"><DayTip row={rows[index]}/></div>}
  </div>;
}

let root;
window.renderUsageHistory = props => {
  root ??= createRoot(document.getElementById('plot'));
  root.render(<HistoryChart {...props}/>);
};
