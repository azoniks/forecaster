const $=id=>document.getElementById(id);let meta,staffingConfig,scheduleData,vacationCapacityData;
const fmt=n=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n);
const pct=n=>`${n>0?'+':''}${fmt(n)}%`;
const roles={shared:'Общая очередь',specialist:'Очереди специалистов',expert:'Очереди экспертов'};
const hrSchemas={
  portrait:{title:'Портрет',description:'Параметры вакансии, условия и портрет кандидата.',fields:[['vacancy_id','ID вакансии'],['vacancy_name','Название вакансии'],['hiring_manager','Нанимающий руководитель'],['staff_department','Подразделение на Стафф'],['training_terms','Условия обучения','textarea'],['line_terms','Условия работы на линии','textarea'],['kpi','Показатели KPI','textarea'],['equipment','Оборудование и требования','textarea'],['interview_checklist','Чек-лист для собеседования','url'],['vacancy_description','Описание вакансии','url'],['hiring_regions','Регионы найма','textarea'],['candidate_profile','Портрет кандидата — смотрим','textarea'],['candidate_restrictions','Портрет кандидата — не смотрим','textarea'],['advantages','Преимущества вакансии','textarea'],['responsibilities','Функционал','textarea'],['selection_stages','Этапы отбора','textarea'],['test_task','Тестовое задание','url']]},
  interview_guide:{title:'Интервью',description:'Скрипты, формат собеседования и правила работы с тестовым.',fields:[['script_url','Скрипт и скрипт КИ','url'],['redirect_url','Редирект','url'],['interviewer_type','Кто проводит собеседование'],['interview_format','Формат собеседования'],['slots','Дни, время и слоты'],['meeting_url','Ссылка на собеседование','url'],['duration','Длительность'],['status_location','Где смотреть статус','textarea'],['oko_vacancy_name','Наименование вакансии в ОКО'],['ki_data','Данные для направления на КИ','textarea'],['test_deadline','Срок выполнения тестового'],['test_review','Процесс проверки ТЗ','textarea'],['test_success','Действия при успешном ТЗ','textarea'],['test_reject','Действия при отказе по ТЗ','textarea']]},
  demand:{title:'Потребность',description:'Плановые места по графикам и подгруппам.',fields:[['schedule','График'],['subgroup','Подгруппа'],['quantity','Количество'],['candidate_surname','Фамилия кандидата']]},
  interviews:{title:'Собеседования',description:'Полная карточка собеседования и результатов тестового.',fields:[['interview_date','Дата собеседования','date'],['interview_time','Время собеседования','time'],['assigned_date','Когда назначили','date'],['recruiter','Рекрутер'],['candidate_name','ФИО кандидата'],['oko_url','ОКО','url'],['push','Пуш'],['attendance','Явка'],['result','Результат'],['reason','Причина'],['comment','Комментарий','textarea'],['test_task','Тестовое'],['test_result','Результат ТЗ'],['final_result','Итог'],['interviewer','Собеседующий']]},
  contacts:{title:'Контакты',description:'Контактные данные сотрудника и дата выхода.',fields:[['candidate_name','ФИО кандидата'],['telegram','Логин в Telegram'],['phone','Телефон','tel'],['yachat','ЯЧАТ'],['start_date','Дата выхода','date']]},
  vacancies:{title:'Вакансии',description:'Площадки и ссылки на опубликованные вакансии.',fields:[['service','Сервис'],['city','Город'],['name','Название'],['url','Ссылка','url']]}
};
const hourOptions=()=>Array.from({length:24},(_,hour)=>`<option value="${hour}">${String(hour).padStart(2,'0')}:00</option>`).join('');
const appSections=['home','forecast','schedule','employees','settings','methodology'];
const sectionTitles={home:'Планирование команды',forecast:'Прогноз нагрузки',schedule:'Графики работы',employees:'Оформление сотрудников',settings:'Настройки прогноза',methodology:'Методология прогноза'};
function sectionFromUrl(){const value=new URLSearchParams(location.search).get('tool')||'home';return appSections.includes(value)?value:'home'}

async function init(){
  try{
    meta=await fetch('/api/meta').then(check);
    const excludedDays=meta.history.excluded_incomplete_dates?.length||0;
    $('dataRange').textContent=`${date(meta.history.first_date)} — ${date(meta.history.last_date)} · ${fmt(meta.history.included_periods)} периодов${excludedDays?` · исключён неполный день: ${meta.history.excluded_incomplete_dates.map(shortDate).join(', ')}`:''}`;
    $('methodDataRange').textContent=`${date(meta.history.first_date)} — ${date(meta.history.last_date)}`;
    $('methodPeriods').textContent=fmt(meta.history.included_periods);
    $('sheet').innerHTML=meta.schedule_sheets.map(s=>`<option ${s===meta.default_schedule_sheet?'selected':''}>${s}</option>`).join('');
    $('scheduleSheet').innerHTML=meta.schedule_sheets.map(s=>`<option ${s===meta.default_schedule_sheet?'selected':''}>${s}</option>`).join('');
    staffingConfig={positions:meta.positions,operation_start_hour:meta.operation_start_hour,operation_end_hour:meta.operation_end_hour};renderPositions();renderMethodCapacities();
    await analyze();
    switchTab(sectionFromUrl(),false);
  }catch(e){showError(e)}
}
async function check(r){const data=await r.json();if(!r.ok)throw new Error(data.error||`Ошибка сервера: ${r.status}`);return data}
function params(){return new URLSearchParams({sheet:$('sheet').value,horizon:$('horizon').value,lookback:$('lookback').value,reserve:$('reserve').value,shared_growth:$('sharedGrowth').value,specialist_growth:$('specialistGrowth').value,expert_growth:$('expertGrowth').value,vacancies:$('vacancies').checked?'1':'0'})}
async function analyze(){
  $('loading').hidden=false;$('dashboard').hidden=true;$('runBtn').disabled=true;
  try{render(await fetch(`/api/analyze?${params()}`).then(check))}catch(e){showError(e)}finally{$('runBtn').disabled=false}
}
function render(data){
  $('loading').hidden=true;$('dashboard').hidden=false;
  const s=data.summary, hires=s.recommendations.reduce((a,r)=>a+r.people,0);
  const change=s.change_percent.overall;$('changeKpi').textContent=pct(change);$('changeKpi').style.color=change>0?'var(--red)':'var(--green)';
  const period=s.comparison_period,direction=change>0?'больше':change<0?'меньше':'столько же';
  $('changeKpiNote').textContent=change===0?`Ожидается столько же обращений, сколько было ${shortDate(period.history_start)}–${shortDate(period.history_end)}`:`В период ${shortDate(period.forecast_start)}–${shortDate(period.forecast_end)} ожидается на ${fmt(Math.abs(change))}% ${direction} обращений, чем было ${shortDate(period.history_start)}–${shortDate(period.history_end)}`;
  $('coverageKpi').textContent=`${fmt(s.sla.actual_percent)}%`;
  $('coverageKpi').style.color=s.sla.meets_target?'var(--green)':'var(--amber)';
  $('coverageKpiNote').textContent=`За ${shortDate(s.sla.period_start)}–${shortDate(s.sla.period_end)}: ${fmt(s.sla.sample_size)} обращений. Цель — не менее ${fmt(s.sla.target_percent)}% в пределах установленного времени.`;
  const failedSla=Object.values(s.sla.groups).filter(item=>!item.meets_target);$('deficitKpi').textContent=failedSla.length?`${failedSla.length} из 3`:'Нет';$('deficitKpiNote').textContent=Object.entries(s.sla.groups).map(([group,item])=>`${roles[group]}: ${fmt(item.actual_percent)}% за ${item.threshold_minutes} мин`).join(' · ');
  $('hireKpi').textContent=hires?`+${hires}`:'Не нужны';
  $('scenarioBadge').textContent=`${data.parameters.horizon_days} дней · резерв ${fmt(data.parameters.reserve_percent)}%`;
  $('groupCards').innerHTML=s.queues.map(queue=>`<article class="group-card"><h3>${escapeHtml(queue.name)}</h3><div class="coverage"><strong>${fmt(queue.coverage_percent)}%</strong><span>в пределах ${queue.sla_threshold_minutes} минут</span></div><div class="bar"><i style="width:${Math.min(100,queue.coverage_percent)}%;background:${queue.coverage_percent<80?'var(--red)':queue.coverage_percent<90?'var(--amber)':'var(--green)'}"></i></div><div class="group-meta"><span><b>${fmt(queue.sla_sample_size)}</b>периодов</span><span><b>${fmt(queue.outside_sla)}</b>вне SLA</span><span><b>${fmt(queue.demand)}</b>прогноз</span></div></article>`).join('');
  renderQuality(s.backtest);renderChart(data.daily);renderRecommendations(s.recommendations);renderRiskWidget(data.heatmap,s.queues);renderStaff(data.schedule);renderCritical(data.critical_hours);
}
function renderQuality(backtest){const quality=backtest.overall,bias=quality.bias_percent,assessment=quality.daily_wape_percent<10?'достигнута':quality.daily_wape_percent<15?'близка к цели':'требует улучшения';$('backtestPeriod').textContent=`${backtest.window_count} окон · ${shortDate(backtest.period_start)}–${shortDate(backtest.period_end)}`;$('backtestDailyWape').textContent=`${fmt(quality.daily_wape_percent)}%`;$('backtestDistribution').textContent=`${fmt(quality.hourly_distribution_error_percent)}%`;$('backtestWape').textContent=`${fmt(quality.wape_percent)}%`;$('backtestBias').textContent=pct(bias);$('backtestBias').style.color=Math.abs(bias)<=5?'var(--green)':'var(--amber)';$('backtestBiasNote').textContent=bias>0?'модель завышает объём':bias<0?'модель занижает объём':'общий объём без смещения';$('backtestExplanation').textContent=`Цель по дневному объёму ${assessment}. Проверено ${backtest.window_count} независимых периодов по 28 дней без ручных корректировок. Суммарный прогноз: ${fmt(quality.predicted)}, факт: ${fmt(quality.actual)}.`;$('backtestWindows').innerHTML=backtest.windows.map(window=>`<div><span>${shortDate(window.period_start)}–${shortDate(window.period_end)}</span><b>день ${fmt(window.overall.daily_wape_percent)}%</b><em>часы ${fmt(window.overall.hourly_distribution_error_percent)}% · ${window.overall.bias_percent>0?'завышение':'занижение'} ${fmt(Math.abs(window.overall.bias_percent))}%</em></div>`).join('')}
function renderChart(rows){const max=Math.max(...rows.map(r=>sum(r.demand)),1);$('dailyChart').innerHTML=rows.map(r=>{const d=sum(r.demand),x=sum(r.deficit);return `<div class="day-bar" data-tip="${date(r.date)} · ${fmt(d)} / дефицит ${fmt(x)}"><i class="demand" style="height:${d/max*100}%"></i><i class="deficit" style="height:${x/max*100}%"></i></div>`}).join('');$('chartDates').textContent=`${date(rows[0].date)} — ${date(rows.at(-1).date)}`}
function renderRecommendations(rows){$('recommendations').innerHTML=rows.length?rows.map(r=>`<div class="rec"><div class="rec-top"><strong>${r.role}</strong><span class="rec-count">+${r.people}</span></div><p>${r.sla_current!==undefined?`Сейчас ${fmt(r.sla_current)}% укладываются в таргет · цель ${fmt(r.sla_target)}% · ${r.shift}`:`${r.shift} · дефицит ${fmt(r.deficit_periods)} периодов · пик ${r.peak_people} чел.`}</p></div>`).join(''):'<div class="ok-state">Все группы выполняют целевой SLA 90%</div>'}
function renderRiskWidget(overall,queues){const select=$('riskQueue'),previous=select.value;select.innerHTML=`<option value="all">Все очереди</option>${queues.map((queue,index)=>`<option value="${index}">${escapeHtml(queue.name)}</option>`).join('')}`;select.value=[...select.options].some(option=>option.value===previous)?previous:'all';const draw=()=>renderHeatmap(select.value==='all'?overall:queues[Number(select.value)].hourly);select.onchange=draw;draw()}
function renderHeatmap(rows){$('heatmap').style.gridTemplateColumns=`repeat(${Math.max(1,rows.length)},minmax(52px,1fr))`;$('heatmap').innerHTML=rows.map(r=>{const coverage=r.demand?Math.max(0,100*(1-r.deficit/r.demand)):100;const bg=coverage>=95?'#eff4f0':coverage>=80?'#f8e7b9':coverage>=60?'#f2b875':'#df7064';return `<div class="heat-cell" style="background:${bg}" title="Средняя нагрузка ${fmt(r.demand)} · не покрыто ${fmt(r.deficit)}"><span>${String(r.hour).padStart(2,'0')}:00</span><b>${fmt(coverage)}%</b><small>${fmt(r.deficit)} не покрыто</small></div>`}).join('')}
function renderStaff(s){$('scheduleName').textContent=s.sheet;$('staff').innerHTML=Object.entries(s.real_people).map(([role,count])=>`<div class="staff-card"><b>${count}</b><span>${role}</span></div>`).join('')}
function renderCritical(rows){$('critical').innerHTML=rows.slice(0,10).map(row=>{const queues=row.queues.slice(0,3).map(queue=>`<span class="risk-tag" title="Не покрыто ${fmt(queue.deficit)}">${escapeHtml(queue.name)} · ${fmt(queue.coverage_percent)}%</span>`).join('');const more=row.queues.length>3?`<small>+ ещё ${row.queues.length-3}</small>`:'';const staffing=row.staffing.length?row.staffing.map(item=>`<span class="staff-need">+${item.people} · ${escapeHtml(item.role)}</span>`).join(''):'<span class="staff-need warning">не назначена подходящая должность</span>';return `<tr><td><b>${date(row.date)}</b><small>${String(row.hour).padStart(2,'0')}:00</small></td><td><b>${fmt(row.coverage_percent)}%</b><small>из ${fmt(row.demand)}</small></td><td><b>${fmt(row.deficit)}</b></td><td><div class="risk-tags">${queues}${more}</div></td><td><div class="staff-needs">${staffing}</div></td></tr>`}).join('')}
function sum(o){return Object.values(o).reduce((a,b)=>a+b,0)}
function date(s){return new Date(`${s}T00:00:00`).toLocaleDateString('ru-RU',{day:'2-digit',month:'short',year:'numeric'})}
function shortDate(s){return s?new Date(`${s}T00:00:00`).toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric'}):'—'}
function showError(e){$('loading').innerHTML=`<div class="error">${e.message}</div>`;$('loading').hidden=false;$('dashboard').hidden=true}
function switchTab(name,updateAddress=true){
  if(!appSections.includes(name))name='home';
  delete document.documentElement.dataset.initialTool;
  document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.tab===name));
  appSections.forEach(tabName=>$(tabName+'Tab').hidden=tabName!==name);
  if(name==='schedule'&&!scheduleData)loadSchedule();
  if(name==='employees')loadHrServices();
  document.title=sectionTitles[name];
  if(updateAddress){
    const url=new URL(location.href);
    if(name==='home')url.searchParams.delete('tool');else url.searchParams.set('tool',name);
    if(url.href!==location.href)history.pushState({tool:name},'',url);
  }
  window.scrollTo({top:0,behavior:'smooth'});
}
function escapeHtml(value){return String(value).replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]))}
function queueSummary(group){const queues=meta.queue_groups?.[group]||[];return queues.length<=2?queues.join(', '):`${queues.slice(0,2).join(', ')} +${queues.length-2}`}
function skillOption(position,group,mode){const checked=(position[mode]||[]).includes(group)?'checked':'';const title=(meta.queue_groups?.[group]||[]).join('\n');return `<label class="skill-option" title="${escapeHtml(title)}"><input type="checkbox" data-skill="${mode}" value="${group}" ${checked}><b>${roles[group]}</b><small>${escapeHtml(queueSummary(group)||'Нет активных очередей')}</small></label>`}
function renderPositions(){
  $('operationStart').innerHTML=hourOptions();$('operationEnd').innerHTML=hourOptions();
  $('operationStart').value=staffingConfig.operation_start_hour;$('operationEnd').value=staffingConfig.operation_end_hour;
  $('positionsEditor').innerHTML=staffingConfig.positions.map(position=>`<div class="position-card" data-id="${escapeHtml(position.id)}"><div class="position-head"><label class="position-enabled"><input type="checkbox" data-field="enabled" ${position.enabled?'checked':''}> Участвует</label><label>Название должности<input data-field="name" value="${escapeHtml(position.name)}"></label><label>Название в графике<input data-field="schedule_role" value="${escapeHtml(position.schedule_role)}"></label><label class="capacity-field">Ёмкость / час<input data-field="capacity" type="number" min="0.1" max="100" step="0.1" value="${position.capacity}"></label><button class="delete-position" data-delete-position title="Удалить">×</button></div><div class="skills-grid"><div class="skill-column"><span>Дневная смена обслуживает</span><div class="skill-options">${Object.keys(roles).map(group=>skillOption(position,group,'groups')).join('')}</div></div><div class="skill-column"><span>Ночная смена обслуживает</span><div class="skill-options">${Object.keys(roles).map(group=>skillOption(position,group,'night_groups')).join('')}</div></div></div></div>`).join('');
  document.querySelectorAll('[data-delete-position]').forEach(button=>button.addEventListener('click',()=>{if(staffingConfig.positions.length<=1)return;staffingConfig.positions=staffingConfig.positions.filter(position=>position.id!==button.closest('.position-card').dataset.id);renderPositions()}));
}
function readPositions(){return [...document.querySelectorAll('.position-card')].map(card=>({id:card.dataset.id,name:card.querySelector('[data-field="name"]').value,schedule_role:card.querySelector('[data-field="schedule_role"]').value,capacity:Number(card.querySelector('[data-field="capacity"]').value),enabled:card.querySelector('[data-field="enabled"]').checked,groups:[...card.querySelectorAll('[data-skill="groups"]:checked')].map(input=>input.value),night_groups:[...card.querySelectorAll('[data-skill="night_groups"]:checked')].map(input=>input.value)}))}
function renderMethodCapacities(){$('methodCapacityCards').innerHTML=staffingConfig.positions.filter(position=>position.enabled).map(position=>`<div><small>${escapeHtml(position.name)}</small><b>${fmt(position.capacity)}</b><span>периода / час</span></div>`).join('')}

async function loadSchedule(){
  $('scheduleLoading').hidden=false;$('scheduleContent').hidden=true;
  try{scheduleData=await fetch(`/api/schedule?sheet=${encodeURIComponent($('scheduleSheet').value)}`).then(check);renderSchedule();refreshVacationCapacity()}
  catch(e){$('scheduleLoading').innerHTML=`<div class="error">${escapeHtml(e.message)}</div>`}
}
async function refreshVacationCapacity(){
  if(!scheduleData)return;
  $('vacationCapacityLoading').hidden=false;$('vacationCapacityContent').hidden=true;
  try{
    vacationCapacityData=await fetch(`/api/vacation-capacity?sheet=${encodeURIComponent(scheduleData.sheet)}`).then(check);
    renderVacationCapacity();$('vacationCapacityLoading').hidden=true;$('vacationCapacityContent').hidden=false;
  }catch(error){$('vacationCapacityLoading').innerHTML=`<div class="error">${escapeHtml(error.message)}</div>`}
}
function renderVacationCapacity(){
  if(!vacationCapacityData)return;
  const labels={peak:'Пик',normal:'Обычная нагрузка',calm:'Спокойный месяц'};
  $('vacationSeasonCards').innerHTML=vacationCapacityData.roles.map(item=>`<div class="vacation-season ${item.season_level}"><span>${escapeHtml(item.name)}</span><b>${labels[item.season_level]}</b><small>индекс ${fmt(item.season_index)}% · лимит ${item.absence_percent}% (${item.absence_limit} чел.)</small></div>`).join('');
  const activityLabels={vacation:'отпуск',sick:'больничный',training:'обучение'},roleNames=vacationCapacityData.roles.map(item=>item.name);
  $('vacationCapacityHead').innerHTML=`<tr><th class="vacation-date-sticky">Дата</th>${roleNames.map(name=>`<th>${escapeHtml(name)}</th>`).join('')}</tr>`;
  $('vacationCapacityBody').innerHTML=vacationCapacityData.dates.map(dateValue=>{const dateObject=new Date(`${dateValue}T00:00:00`),weekend=[0,6].includes(dateObject.getDay());const cells=roleNames.map(name=>{const item=vacationCapacityData.rows.find(row=>row.date===dateValue&&row.position_name===name);if(!item)return'<td>—</td>';const absences=item.absences.map(value=>`${escapeHtml(value.name)} — ${activityLabels[value.activity]}`).join('<br>');const subbotnik=item.vacation_subbotnik;const status=item.available>0?`Можно ещё: ${item.available}`:item.policy_available===0?'Лимит исчерпан':'Нужен субботник';return `<td class="vacation-month-cell ${subbotnik?'critical':item.available?'available':'full'}"><div class="vacation-cell-head"><strong>${status}</strong><span>${item.absent} из ${item.absence_limit} отсутствуют</span></div>${absences?`<small class="vacation-absence-list">${absences}</small>`:''}${subbotnik?`<div class="vacation-subbotnik"><b>Субботник: 1 чел.</b><span>${subbotnik.shift}</span><small>для ещё одного отпуска</small></div>`:''}</td>`}).join('');return `<tr class="${weekend?'vacation-weekend':''}"><td class="vacation-date-sticky"><b>${shortDate(dateValue)}</b><small>${dateObject.toLocaleDateString('ru-RU',{weekday:'short'})}</small></td>${cells}</tr>`}).join('');
}
async function createScheduleMonth(){
  if(!scheduleData)return;
  const source=scheduleData.sheet;
  if(!window.confirm(`Создать следующий месяц на основе графика «${source}»?\n\n2/2 продолжится без сброса цикла. 5/2 будет работать по будням, кроме праздников.`))return;
  const button=$('createScheduleMonth');button.disabled=true;showScheduleStatus('Создаём новый месяц…','success');
  try{
    const result=await fetch('/api/schedule-months',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source})}).then(check);
    scheduleData=result.schedule;
    meta=await fetch('/api/meta').then(check);
    $('sheet').innerHTML=meta.schedule_sheets.map(s=>`<option ${s===meta.default_schedule_sheet?'selected':''}>${s}</option>`).join('');
    $('scheduleSheet').innerHTML=meta.schedule_sheets.map(s=>`<option>${escapeHtml(s)}</option>`).join('');$('scheduleSheet').value=scheduleData.sheet;
    renderSchedule();showScheduleStatus(`График «${scheduleData.sheet}» создан автоматически`,'success');
  }catch(error){showScheduleStatus(error.message,'failure')}finally{button.disabled=false}
}
function renderSchedule(){
  $('scheduleLoading').hidden=true;$('scheduleContent').hidden=false;
  renderScheduleSegmentation();
  const s=scheduleData.summary||{};
  const visibleEmployees=filteredScheduleEmployees(),roleCounts=visibleEmployees.reduce((result,employee)=>(result[employee.role]=(result[employee.role]||0)+1,result),{});
  $('scheduleRoleSummary').innerHTML=`<div class="role-summary role-total"><span>Σ</span><div><b>${$('scheduleActivityFilter').value==='all'?'Вся команда':'По выбранному типу'}</b><small>${visibleEmployees.length} сотрудников</small></div></div>`+Object.entries(roleCounts).map(([role,count])=>`<div class="role-summary ${scheduleRoleClass(role)}"><span></span><div><b>${escapeHtml(role)}</b><small>${count} ${count===1?'сотрудник':'сотрудников'}</small></div></div>`).join('');
  const patterns=[...new Set(visibleEmployees.map(employee=>employeeSchedulePattern(employee)).filter(Boolean))];
  $('schedulePatternLegend').innerHTML=patterns.map(pattern=>`<span><i style="background:${schedulePalette(pattern).accent}"></i>${escapeHtml(pattern)}</span>`).join('');
  renderManagerSchedule();
  const roles=[...new Set(scheduleData.employees.filter(employee=>!employee.is_vacancy).map(employee=>employee.role))];
  const selectedRole=roles.includes($('bulkScheduleRole').value)?$('bulkScheduleRole').value:roles[0]||'';
  $('bulkScheduleRole').innerHTML=roles.map(role=>`<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`).join('');$('bulkScheduleRole').value=selectedRole;renderBulkEmployees();
}
function renderScheduleSegmentation(resetEmployee=false){
  const roleSelect=$('scheduleRoleFilter'),employeeSelect=$('scheduleEmployeeFilter'),previousRole=roleSelect.value||'all',previousEmployee=resetEmployee?'all':employeeSelect.value||'all';
  const roles=[...new Set(scheduleData.employees.map(employee=>employee.role))];
  roleSelect.innerHTML=`<option value="all">Все должности</option>${roles.map(role=>`<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`).join('')}`;
  roleSelect.value=roles.includes(previousRole)?previousRole:'all';
  const employees=scheduleData.employees.filter(employee=>roleSelect.value==='all'||employee.role===roleSelect.value);
  employeeSelect.innerHTML=`<option value="all">Все сотрудники</option>${employees.map(employee=>`<option value="${escapeHtml(employee.id)}">${escapeHtml(employee.name)}${roleSelect.value==='all'?` · ${escapeHtml(employee.role)}`:''}</option>`).join('')}`;
  employeeSelect.value=employees.some(employee=>employee.id===previousEmployee)?previousEmployee:'all';
}
function filteredScheduleEmployees(){
  const type=$('scheduleActivityFilter').value,role=$('scheduleRoleFilter').value,employeeId=$('scheduleEmployeeFilter').value;
  return scheduleData.employees.filter(employee=>{
    if(role!=='all'&&employee.role!==role)return false;
    if(employeeId!=='all'&&employee.id!==employeeId)return false;
    if(type==='all')return true;
    if(type==='work')return Object.keys(employee.shifts||{}).length>0;
    if(type==='day_off')return scheduleData.dates.some(date=>!employee.shifts?.[date]&&!employee.activities?.[date]);
    return Object.values(employee.activities||{}).includes(type);
  });
}
function stringHash(value){return [...String(value)].reduce((hash,char)=>((hash<<5)-hash+char.charCodeAt(0))|0,0)}
const scheduleColors=[['#287f6b','#e6f4f0'],['#4269c7','#e9eefb'],['#a0662f','#f8eee4'],['#8a55ad','#f3eafb'],['#b34f65','#fae9ed'],['#367f9e','#e7f3f7'],['#777a2f','#f2f3df'],['#ce772c','#fff0e2']];
function employeeSchedulePattern(employee){
  if(employee.schedule_pattern)return employee.schedule_pattern.replace(/-/g,'–');
  const shifts=Object.values(employee.shifts||{}),counts=shifts.reduce((result,shift)=>(result[shift]=(result[shift]||0)+1,result),{});
  const shift=Object.entries(counts).sort((a,b)=>b[1]-a[1])[0]?.[0];
  return shift?`Смена ${shift.replace('/', '–')}`:'Без графика';
}
function schedulePalette(pattern){const colors=scheduleColors[Math.abs(stringHash(pattern))%scheduleColors.length];return{accent:colors[0],soft:colors[1]}}
function crc32(bytes){let crc=-1;for(const byte of bytes){crc^=byte;for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0)}return(crc^-1)>>>0}
function joinBytes(parts){const size=parts.reduce((sum,part)=>sum+part.length,0),result=new Uint8Array(size);let offset=0;parts.forEach(part=>{result.set(part,offset);offset+=part.length});return result}
function zipScheduleImages(files){
  const encoder=new TextEncoder(),localParts=[],centralParts=[];let offset=0;
  const write=(view,position,value,size)=>{for(let index=0;index<size;index++)view[position+index]=(value>>>(index*8))&255};
  files.forEach(file=>{
    const name=encoder.encode(file.name),crc=crc32(file.data),local=new Uint8Array(30+name.length),central=new Uint8Array(46+name.length);
    write(local,0,0x04034b50,4);write(local,4,20,2);write(local,6,0x0800,2);write(local,14,crc,4);write(local,18,file.data.length,4);write(local,22,file.data.length,4);write(local,26,name.length,2);local.set(name,30);
    write(central,0,0x02014b50,4);write(central,4,20,2);write(central,6,20,2);write(central,8,0x0800,2);write(central,16,crc,4);write(central,20,file.data.length,4);write(central,24,file.data.length,4);write(central,28,name.length,2);write(central,42,offset,4);central.set(name,46);
    localParts.push(local,file.data);centralParts.push(central);offset+=local.length+file.data.length;
  });
  const central=joinBytes(centralParts),end=new Uint8Array(22);write(end,0,0x06054b50,4);write(end,8,files.length,2);write(end,10,files.length,2);write(end,12,central.length,4);write(end,16,offset,4);
  return new Blob([...localParts,central,end],{type:'application/zip'});
}
function downloadScheduleImage(){
  if(!scheduleData)return;
  const allEmployees=filteredScheduleEmployees(),dates=scheduleData.dates;
  if(!allEmployees.length){showScheduleStatus('Нет строк для выгрузки','failure');return}
  const roleGroups=[...new Set(scheduleData.employees.map(employee=>employee.role))].map(role=>[role,allEmployees.filter(employee=>employee.role===role)]).filter(([,employees])=>employees.length);
  const images=[];
  roleGroups.forEach(([exportRole,employees])=>{
  const scale=2,nameWidth=300,roleWidth=150,cellWidth=58,topHeight=104,rowHeight=38,groupHeight=34;
  let groups=0,lastRole='';employees.forEach(employee=>{if(employee.role!==lastRole){groups++;lastRole=employee.role}});
  const width=nameWidth+roleWidth+dates.length*cellWidth,height=topHeight+employees.length*rowHeight+groups*groupHeight+18;
  const canvas=document.createElement('canvas');canvas.width=width*scale;canvas.height=height*scale;
  const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.fillStyle='#fff';ctx.fillRect(0,0,width,height);
  ctx.textBaseline='middle';ctx.strokeStyle='#cfd6d1';ctx.lineWidth=1;
  const month=new Date(`${dates[0]}T00:00:00`).toLocaleDateString('ru-RU',{month:'long',year:'numeric'}).toUpperCase();
  ctx.fillStyle='#26322b';ctx.fillRect(0,0,width,48);ctx.fillStyle='#fff';ctx.font='700 20px Arial';ctx.fillText(`ГРАФИК · ${month}`,20,25);
  const filterLabel=$('scheduleActivityFilter').selectedOptions[0]?.textContent||'Все типы';ctx.font='12px Arial';ctx.textAlign='right';ctx.fillStyle='#dce9e1';ctx.fillText(`${exportRole} · ${filterLabel}`,width-18,25);ctx.textAlign='left';
  ctx.fillStyle='#f4f7f5';ctx.fillRect(0,48,width,56);ctx.fillStyle='#26322b';ctx.font='700 12px Arial';ctx.fillText('Сотрудник',12,76);ctx.fillText('Должность',nameWidth+10,76);
  dates.forEach((dateValue,index)=>{const dateObject=new Date(`${dateValue}T00:00:00`),x=nameWidth+roleWidth+index*cellWidth,weekend=[0,6].includes(dateObject.getDay());ctx.fillStyle=weekend?'#fff1e7':'#f4f7f5';ctx.fillRect(x,48,cellWidth,56);ctx.strokeRect(x,48,cellWidth,56);ctx.textAlign='center';ctx.fillStyle=weekend?'#c0573c':'#26322b';ctx.font='700 12px Arial';ctx.fillText(String(dateObject.getDate()),x+cellWidth/2,68);ctx.font='10px Arial';ctx.fillText(dateObject.toLocaleDateString('ru-RU',{weekday:'short'}),x+cellWidth/2,87)});ctx.textAlign='left';
  const activityLabels={vacation:'ОТП',sick:'БЛ',training:'ОБУЧ'};let y=topHeight;lastRole='';
  employees.forEach(employee=>{
    const pattern=employeeSchedulePattern(employee),patternColors=schedulePalette(pattern);
    if(employee.role!==lastRole){lastRole=employee.role;ctx.fillStyle='#eef1ef';ctx.fillRect(0,y,width,groupHeight);ctx.fillStyle='#26322b';ctx.font='700 13px Arial';ctx.fillText(employee.role,12,y+groupHeight/2);y+=groupHeight}
    ctx.fillStyle='#fff';ctx.fillRect(0,y,nameWidth+roleWidth,rowHeight);ctx.fillStyle=patternColors.accent;ctx.fillRect(0,y,6,rowHeight);ctx.strokeStyle='#d9dfdb';ctx.strokeRect(0,y,width,rowHeight);ctx.fillStyle='#1f2923';ctx.font='13px Arial';ctx.fillText(employee.name,16,y+rowHeight/2);ctx.fillStyle='#66716a';ctx.font='11px Arial';ctx.fillText(employee.role,nameWidth+10,y+rowHeight/2);
    dates.forEach((dateValue,index)=>{const x=nameWidth+roleWidth+index*cellWidth,activity=employee.activities?.[dateValue],shift=employee.shifts?.[dateValue];ctx.fillStyle=activity==='training'?'#a9d08e':activity==='sick'?'#e06666':activity==='vacation'?'#ff4b45':shift?patternColors.soft:'#fff';ctx.fillRect(x,y,cellWidth,rowHeight);if(shift){ctx.fillStyle=patternColors.accent;ctx.fillRect(x,y,3,rowHeight)}ctx.strokeStyle='#cfd6d1';ctx.strokeRect(x,y,cellWidth,rowHeight);ctx.textAlign='center';ctx.fillStyle=activity?'#172019':shift?'#172019':'#a5ada8';ctx.font=activity?'700 9px Arial':'12px Arial';ctx.fillText(activity?activityLabels[activity]:shift||'',x+cellWidth/2,y+rowHeight/2)});ctx.textAlign='left';y+=rowHeight;
  });
  const dataUrl=canvas.toDataURL('image/png'),binary=atob(dataUrl.split(',')[1]),bytes=new Uint8Array(binary.length);for(let index=0;index<binary.length;index++)bytes[index]=binary.charCodeAt(index);
  images.push({name:`график-${scheduleData.sheet}-${exportRole.toLowerCase().replace(/\s+/g,'-')}-${filterLabel.toLowerCase().replace(/\s+/g,'-')}.png`,data:bytes});
  });
  const link=document.createElement('a'),url=URL.createObjectURL(zipScheduleImages(images));link.href=url;link.download=`графики-${scheduleData.sheet}.zip`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  showScheduleStatus(`Скачан архив: ${roleGroups.length} изображения`,'success');
}
function renderBulkEmployees(){
  const role=$('bulkScheduleRole').value,select=$('bulkScheduleEmployee'),previous=select.value;
  const employees=(scheduleData?.employees||[]).filter(employee=>!employee.is_vacancy&&employee.role===role);
  select.innerHTML=employees.map(employee=>`<option value="${escapeHtml(employee.id)}">${escapeHtml(employee.name)}</option>`).join('');
  if(employees.some(employee=>employee.id===previous))select.value=previous;
}
function renderManagerSchedule(){
  const dates=scheduleData.dates;
  $('scheduleHead').innerHTML=`<tr><th class="employee-sticky">Сотрудник</th><th class="role-sticky">Должность</th>${dates.map(d=>`<th class="${[0,6].includes(new Date(`${d}T00:00:00`).getDay())?'weekend':''}"><b>${new Date(`${d}T00:00:00`).toLocaleDateString('ru-RU',{day:'2-digit'})}</b><small>${new Date(`${d}T00:00:00`).toLocaleDateString('ru-RU',{weekday:'short'})}</small></th>`).join('')}<th>Часы</th></tr>`;
  let previousRole='';const rows=[];
  const labels={vacation:'О',sick:'Б',training:'У'},titles={vacation:'Отпуск',sick:'Больничный',training:'Обучение'};
  filteredScheduleEmployees().forEach(employee=>{
    const pattern=employeeSchedulePattern(employee),palette=schedulePalette(pattern),style=`--schedule-color:${palette.accent};--schedule-soft:${palette.soft}`;
    if(employee.role!==previousRole){previousRole=employee.role;rows.push(`<tr class="role-group-row ${scheduleRoleClass(employee.role)}" style="${style}"><td class="employee-sticky"><div class="role-group-label"><span></span><b>${escapeHtml(employee.role)}</b></div></td><td class="role-sticky"></td><td class="role-group-fill" colspan="${dates.length+1}"></td></tr>`)}
    const dayCells=dates.map(d=>{const activity=employee.activities?.[d],shift=employee.shifts[d],note=employee.notes?.[d]||'',title=[activity?titles[activity]:shift?`Смена ${shift}`:'Выходной',note?`Примечание: ${note}`:''].filter(Boolean).join(' · ');return `<td class="shift-cell ${activity?`activity-${activity}`:shift?'working':''} ${note?'has-note':''}" title="${escapeHtml(title)}" data-employee="${escapeHtml(employee.id)}" data-date="${d}" data-shift="${escapeHtml(shift||'')}" data-activity="${activity||'day_off'}" data-note="${escapeHtml(note)}">${activity?`<span class="activity-marker">${labels[activity]}</span>`:shift?escapeHtml(shift):'—'}</td>`}).join('');
    rows.push(`<tr data-schedule-row data-employee-id="${escapeHtml(employee.id)}" data-role="${escapeHtml(employee.role)}" style="${style}" class="employee-schedule-row ${scheduleRoleClass(employee.role)} ${employee.is_vacancy?'vacancy-row':''}"><td class="employee-sticky"><div class="employee-cell"><button class="drag-handle" draggable="true" type="button" title="Зажмите и перетащите строку">⋮⋮</button><i class="employee-color-marker" title="${escapeHtml(pattern)}"></i><span><b>${escapeHtml(employee.name)}</b><small>${escapeHtml(employee.login||'без логина')}</small></span><button class="edit-schedule-employee" type="button" title="Редактировать сотрудника">✎</button><button class="delete-schedule-employee" type="button" title="Удалить из графика">🗑</button></div></td><td class="role-sticky">${escapeHtml(employee.role)}</td>${dayCells}<td class="hours-total">${fmt(employee.hours)}</td></tr>`);
  });
  $('scheduleBody').innerHTML=rows.join('');
  document.querySelectorAll('.shift-cell').forEach(cell=>cell.addEventListener('click',()=>editShift(cell)));
  setupScheduleRowActions();
}
function setupScheduleRowActions(){
  let dragged=null,dropped=false;
  document.querySelectorAll('[data-schedule-row]').forEach(row=>{
    const handle=row.querySelector('.drag-handle'),editButton=row.querySelector('.edit-schedule-employee'),deleteButton=row.querySelector('.delete-schedule-employee');
    handle.addEventListener('dragstart',event=>{if($('scheduleActivityFilter').value!=='all'){event.preventDefault();showScheduleStatus('Для сортировки выберите «Все типы»','failure');return}dragged=row;dropped=false;row.classList.add('dragging');event.dataTransfer.setData('text/plain',row.dataset.employeeId);event.dataTransfer.effectAllowed='move'});
    handle.addEventListener('dragend',()=>{row.classList.remove('dragging');if(dragged&&!dropped)renderSchedule();dragged=null});
    row.addEventListener('dragover',event=>{if(dragged&&dragged.dataset.role===row.dataset.role){event.preventDefault();const box=row.getBoundingClientRect();row.parentNode.insertBefore(dragged,event.clientY<box.top+box.height/2?row:row.nextSibling)}});
    row.addEventListener('drop',async event=>{event.preventDefault();if(!dragged)return;dropped=true;await saveScheduleOrder()});
    deleteButton.addEventListener('pointerdown',event=>event.stopPropagation());
    deleteButton.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();deleteScheduleEmployee(row.dataset.employeeId,row.querySelector('.employee-cell b').textContent)});
    editButton.addEventListener('pointerdown',event=>event.stopPropagation());
    editButton.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();showScheduleEditEmployee(row.dataset.employeeId)});
  });
}
async function saveScheduleOrder(){
  const visibleIds=[...document.querySelectorAll('[data-schedule-row]')].map(row=>row.dataset.employeeId);
  try{const result=await fetch('/api/schedule-order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sheet:scheduleData.sheet,employee_ids:visibleIds})}).then(check);scheduleData=result.schedule;renderSchedule();showScheduleStatus('Порядок сохранён','success')}catch(error){renderSchedule();showScheduleStatus(error.message,'failure')}
}
async function deleteScheduleEmployee(employeeId,name){
  if(!window.confirm(`Удалить «${name}» из графика за ${scheduleData.sheet}?`))return;
  try{const result=await fetch('/api/schedule-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sheet:scheduleData.sheet,employee_id:employeeId})}).then(check);scheduleData=result.schedule;renderSchedule();showScheduleStatus('Сотрудник удалён из графика','success')}catch(error){showScheduleStatus(error.message,'failure')}
}
function showScheduleEditEmployee(employeeId){
  const employee=scheduleData.employees.find(item=>item.id===employeeId);if(!employee)return;
  const form=$('scheduleEditEmployeeForm');form.elements.employee_id.value=employee.id;form.elements.name.value=employee.name;form.elements.login.value=employee.login;
  $('scheduleEditEmployeeRole').innerHTML=meta.positions.map(position=>`<option value="${escapeHtml(position.schedule_role||position.name)}">${escapeHtml(position.name)}</option>`).join('');$('scheduleEditEmployeeRole').value=employee.role;
  form.hidden=false;form.scrollIntoView({behavior:'smooth',block:'center'});
}
async function submitScheduleEditEmployee(event){
  event.preventDefault();const form=event.currentTarget,status=$('scheduleEditEmployeeFormStatus'),button=form.querySelector('[type=submit]');button.disabled=true;status.textContent='Сохраняем…';
  try{const result=await fetch('/api/schedule-employee-update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...Object.fromEntries(new FormData(form).entries()),sheet:scheduleData.sheet})}).then(check);scheduleData=result.schedule;form.hidden=true;status.textContent='';renderSchedule();showScheduleStatus('Сотрудник обновлён','success')}catch(error){status.className='failure';status.textContent=error.message}finally{button.disabled=false}
}
function scheduleRoleClass(role){const value=String(role).toLowerCase();if(value.includes('младш'))return'role-junior';if(value.includes('эксперт'))return'role-expert';return'role-specialist'}
function showScheduleEmployeeForm(){
  $('scheduleEmployeeRole').innerHTML=meta.positions.map(position=>`<option value="${escapeHtml(position.schedule_role||position.name)}">${escapeHtml(position.name)}</option>`).join('');$('scheduleEmployeeForm').hidden=false;$('scheduleEmployeeForm').scrollIntoView({behavior:'smooth',block:'center'});
}
async function submitScheduleEmployee(event){
  event.preventDefault();const form=event.currentTarget,status=$('scheduleEmployeeFormStatus'),button=form.querySelector('[type=submit]');button.disabled=true;status.textContent='Сохраняем…';
  try{const result=await fetch('/api/schedule-employees',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...Object.fromEntries(new FormData(form).entries()),sheet:$('scheduleSheet').value})}).then(check);scheduleData=result.schedule;form.reset();form.hidden=true;status.textContent='';renderSchedule();showScheduleStatus('Сотрудник добавлен','success')}
  catch(e){status.className='failure';status.textContent=e.message}finally{button.disabled=false}
}
function showBulkScheduleForm(){
  const form=$('scheduleBulkForm'),dates=scheduleData?.dates||[];form.hidden=false;
  if(dates.length){$('bulkScheduleFrom').value=dates[0];$('bulkScheduleTo').value=dates[dates.length-1]}
  form.scrollIntoView({behavior:'smooth',block:'center'});
}
function toggleBulkShift(){$('bulkScheduleShiftWrap').hidden=$('bulkScheduleActivity').value!=='work'}
async function submitBulkSchedule(event){
  event.preventDefault();const form=event.currentTarget,status=$('scheduleBulkFormStatus'),button=form.querySelector('[type=submit]');button.disabled=true;status.textContent='Применяем…';
  try{const result=await fetch('/api/schedule-bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...Object.fromEntries(new FormData(form).entries()),sheet:scheduleData.sheet})}).then(check);scheduleData=result.schedule;form.hidden=true;status.textContent='';renderSchedule();refreshVacationCapacity();showScheduleStatus('Период обновлён','success')}
  catch(e){status.className='failure';status.textContent=e.message}finally{button.disabled=false}
}
async function editShift(cell){
  if(cell.querySelector('.shift-popover'))return;
  const previous=cell.innerHTML,activity=cell.dataset.activity||'day_off',shift=cell.dataset.shift||'',note=cell.dataset.note||'';
  cell.innerHTML=`<div class="shift-popover"><select><option value="work">Работа</option><option value="day_off">Выходной</option><option value="vacation">Отпуск</option><option value="sick">Больничный</option><option value="training">Обучение</option></select><input value="${escapeHtml(shift)}" placeholder="8/20"><textarea maxlength="500" placeholder="Примечание к этому дню">${escapeHtml(note)}</textarea><div><button type="button" data-save>Сохранить</button><button type="button" data-cancel>×</button></div></div>`;
  const editor=cell.querySelector('.shift-popover'),select=editor.querySelector('select'),input=editor.querySelector('input'),noteInput=editor.querySelector('textarea');select.value=activity;const toggle=()=>input.hidden=select.value!=='work';toggle();select.addEventListener('change',toggle);
  editor.querySelector('[data-cancel]').addEventListener('click',e=>{e.stopPropagation();cell.innerHTML=previous});
  editor.querySelector('[data-save]').addEventListener('click',async e=>{e.stopPropagation();cell.textContent='…';try{const result=await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sheet:scheduleData.sheet,employee_id:cell.dataset.employee,date:cell.dataset.date,shift:input.value.trim(),activity:select.value,note:noteInput.value.trim()})}).then(check);scheduleData=result.schedule;renderSchedule();refreshVacationCapacity();showScheduleStatus('Занятость и примечание сохранены','success')}catch(error){cell.innerHTML=previous;showScheduleStatus(error.message,'failure')}});
}
function showScheduleStatus(message,type){const status=$('scheduleStatus');status.textContent=message;status.className=`schedule-status ${type}`;setTimeout(()=>status.textContent='',2500)}
function switchScheduleSection(section){
  const guide=section==='guide';
  $('scheduleWorkspace').hidden=guide;$('scheduleGuide').hidden=!guide;
  document.querySelectorAll('[data-schedule-section]').forEach(button=>button.classList.toggle('active',button.dataset.scheduleSection===section));
}
let activeHrSection='onboarding',activeHrService='metrika';
async function loadHrServices(){
  try{const data=await fetch('/api/hr-services').then(check),select=$('hrServiceSelect'),previous=select.value||activeHrService;select.innerHTML=data.services.map(service=>`<option value="${escapeHtml(service.id)}">${escapeHtml(service.name)}</option>`).join('');activeHrService=data.services.some(service=>service.id===previous)?previous:data.services[0].id;select.value=activeHrService;if(activeHrSection==='onboarding')loadEmployees();else if(hrSchemas[activeHrSection])loadHrRecords(activeHrSection)}
  catch(e){$('employeeFormStatus').textContent=e.message}
}
async function addHrService(){
  const name=window.prompt('Название нового сервиса');if(!name?.trim())return;
  try{const result=await fetch('/api/hr-services',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim()})}).then(check);activeHrService=result.service.id;await loadHrServices()}
  catch(e){window.alert(e.message)}
}
function switchHrSection(section){
  activeHrSection=section;document.querySelectorAll('[data-hr-section]').forEach(button=>button.classList.toggle('active',button.dataset.hrSection===section));
  const onboarding=section==='onboarding',funnel=section==='funnel',schema=hrSchemas[section];
  $('employeeForm').hidden=!onboarding;$('employeeRegistry').hidden=true;$('toggleEmployeeRegistry').hidden=!onboarding;$('hrDynamicPanel').hidden=onboarding||funnel;$('funnelPanel').hidden=!funnel;
  $('hrSectionTitle').textContent=onboarding?'Оформления':funnel?'Воронка':schema.title;
  $('hrSectionDescription').textContent=onboarding?'Все данные из листа «Оформления».':funnel?'Автоматические показатели процесса найма.':schema.description;
  if(schema){$('hrFormTitle').textContent=`Новая запись · ${schema.title}`;$('hrDynamicFields').innerHTML=schema.fields.map(([name,label,type='text'])=>hrField(name,label,type)).join('');loadHrRecords(section)}
}
function hrField(name,label,type){if(type==='textarea')return `<label class="field-span-2">${label}<textarea name="${name}" rows="3"></textarea></label>`;return `<label>${label}<input name="${name}" type="${type}"></label>`}
async function loadHrRecords(section){
  try{const data=await fetch(`/api/hr-records?section=${encodeURIComponent(section)}&service=${encodeURIComponent(activeHrService)}`).then(check);$('hrRecordCount').textContent=`${data.records.length} записей`;$('hrRecordList').innerHTML=data.records.length?data.records.map(record=>{const values=Object.entries(record).filter(([key,value])=>!['id','created_at','service_id'].includes(key)&&value).slice(0,4);return `<div class="hr-record-row">${values.map(([key,value],index)=>`<span><small>${escapeHtml((hrSchemas[section].fields.find(field=>field[0]===key)||[key,key])[1])}</small><b>${escapeHtml(value)}</b></span>`).join('')}</div>`}).join(''):'<div class="empty-registry">Записей пока нет</div>'}catch(e){$('hrFormStatus').textContent=e.message}
}
async function submitHrRecord(event){
  event.preventDefault();const form=event.currentTarget,status=$('hrFormStatus'),button=form.querySelector('[type=submit]');button.disabled=true;status.textContent='Сохраняем…';
  try{await fetch(`/api/hr-records?section=${encodeURIComponent(activeHrSection)}&service=${encodeURIComponent(activeHrService)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form).entries()))}).then(check);form.reset();status.className='success';status.textContent='Запись сохранена';await loadHrRecords(activeHrSection)}catch(e){status.className='failure';status.textContent=e.message}finally{button.disabled=false}
}
async function loadEmployees(){
  try{const data=await fetch(`/api/employees?service=${encodeURIComponent(activeHrService)}`).then(check);renderEmployees(data.employees)}
  catch(e){$('employeeFormStatus').textContent=e.message;$('employeeFormStatus').className='failure'}
}
function renderEmployees(rows){
  $('employeeCount').textContent=`${rows.length} ${rows.length===1?'запись':'записей'}`;
  $('employeeList').innerHTML=rows.length?rows.map(employee=>`<div class="employee-row"><div class="employee-avatar">${escapeHtml(employee.full_name.split(/\s+/).slice(0,2).map(part=>part[0]||'').join(''))}</div><div><b>${escapeHtml(employee.full_name)}</b><small>${escapeHtml(employee.staff_login||'логин не указан')} · выход ${employee.start_date?shortDate(employee.start_date):'не указан'}</small></div><span>${escapeHtml(employee.schedule||'график не указан')}</span><em>${escapeHtml(employee.status||'без статуса')}</em></div>`).join(''):'<div class="empty-registry">Сотрудники ещё не добавлены</div>';
}
async function submitEmployee(event){
  event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type="submit"]'),status=$('employeeFormStatus');button.disabled=true;status.className='';status.textContent='Сохраняем…';
  const payload={...Object.fromEntries(new FormData(form).entries()),service_id:activeHrService};
  try{await fetch('/api/employees',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(check);form.reset();status.className='success';status.textContent='Сотрудник добавлен в реестр';await loadEmployees();$('employeeRegistry').hidden=false}
  catch(e){status.className='failure';status.textContent=e.message}finally{button.disabled=false}
}
function addPosition(){staffingConfig.positions.push({id:`position_${Date.now()}`,name:'Новая должность',schedule_role:'Новая должность',capacity:4,enabled:true,groups:['shared'],night_groups:['shared']});renderPositions()}
async function savePositions(){const button=$('savePositionsBtn'),status=$('configStatus');button.disabled=true;status.className='config-status';status.textContent='Сохраняем…';try{const result=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_start_hour:Number($('operationStart').value),operation_end_hour:Number($('operationEnd').value),positions:readPositions()})}).then(check);staffingConfig=result.config;meta=await fetch('/api/meta').then(check);renderPositions();renderMethodCapacities();status.className='config-status success';status.textContent='Настройки сохранены';await analyze()}catch(e){status.className='config-status failure';status.textContent=e.message}finally{button.disabled=false}}
async function uploadHistory(file){
  const status=$('uploadStatus'),button=$('uploadHistoryBtn');
  status.hidden=false;status.className='upload-status working';status.textContent='Загружаем и пересобираем прогноз…';button.disabled=true;
  try{
    const result=await fetch('/api/history',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file}).then(check);
    status.className='upload-status success';status.textContent=`Готово: ${fmt(result.history.included_periods)} периодов`;
    meta=await fetch('/api/meta').then(check);
    $('dataRange').textContent=`${date(meta.history.first_date)} — ${date(meta.history.last_date)} · ${fmt(meta.history.included_periods)} периодов`;
    $('methodDataRange').textContent=`${date(meta.history.first_date)} — ${date(meta.history.last_date)}`;$('methodPeriods').textContent=fmt(meta.history.included_periods);
    await analyze();switchTab('forecast');
  }catch(e){status.className='upload-status failure';status.textContent=e.message}finally{button.disabled=false;$('historyFile').value=''}
}
async function uploadQueues(file){const status=$('queueUploadStatus'),button=$('uploadQueuesBtn');status.hidden=false;status.className='upload-status working';status.textContent='Проверяем очереди и пересобираем историю…';button.disabled=true;try{const result=await fetch('/api/queues',{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},body:file}).then(check);status.className='upload-status success';status.textContent=`Готово: ${result.queue_count} очередей участвуют в прогнозе`;meta=await fetch('/api/meta').then(check);staffingConfig={positions:meta.positions,operation_start_hour:meta.operation_start_hour,operation_end_hour:meta.operation_end_hour};$('dataRange').textContent=`${date(meta.history.first_date)} — ${date(meta.history.last_date)} · ${fmt(meta.history.included_periods)} периодов`;$('methodDataRange').textContent=`${date(meta.history.first_date)} — ${date(meta.history.last_date)}`;$('methodPeriods').textContent=fmt(meta.history.included_periods);renderPositions();renderMethodCapacities();await analyze()}catch(e){status.className='upload-status failure';status.textContent=e.message}finally{button.disabled=false;$('queueFile').value=''}}
document.querySelectorAll('.tab').forEach(tab=>tab.addEventListener('click',()=>switchTab(tab.dataset.tab)));document.querySelectorAll('[data-open-tool]').forEach(button=>button.addEventListener('click',()=>switchTab(button.dataset.openTool)));document.querySelectorAll('[data-hr-section]').forEach(button=>button.addEventListener('click',()=>switchHrSection(button.dataset.hrSection)));$('homeBtn').addEventListener('click',()=>switchTab('home'));$('employeeForm').addEventListener('submit',submitEmployee);$('hrDynamicForm').addEventListener('submit',submitHrRecord);$('toggleEmployeeRegistry').addEventListener('click',()=>{const registry=$('employeeRegistry');registry.hidden=!registry.hidden;$('toggleEmployeeRegistry').textContent=registry.hidden?'Показать реестр':'Скрыть реестр'});$('scheduleSheet').addEventListener('change',()=>{scheduleData=null;loadSchedule()});$('scheduleActivityFilter').addEventListener('change',renderSchedule);$('addPositionBtn').addEventListener('click',addPosition);$('savePositionsBtn').addEventListener('click',savePositions);$('uploadHistoryBtn').addEventListener('click',()=>$('historyFile').click());$('historyFile').addEventListener('change',e=>{const file=e.target.files[0];if(!file)return;$('historyFileLabel').textContent=`${file.name} · ${fmt(file.size/1024/1024)} МБ`;uploadHistory(file)});$('uploadQueuesBtn').addEventListener('click',()=>$('queueFile').click());$('queueFile').addEventListener('change',e=>{const file=e.target.files[0];if(!file)return;$('queueFileLabel').textContent=`${file.name} · ${fmt(file.size/1024/1024)} МБ`;uploadQueues(file)});$('reserve').addEventListener('input',e=>$('reserveValue').textContent=`${e.target.value}%`);$('runBtn').addEventListener('click',async()=>{await analyze();switchTab('forecast')});$('resetBtn').addEventListener('click',()=>{['sharedGrowth','specialistGrowth','expertGrowth'].forEach(id=>$(id).value=0);$('horizon').value=28;$('lookback').value=12;$('reserve').value=15;$('reserveValue').textContent='15%';$('vacancies').checked=false;$('sheet').value=meta.default_schedule_sheet});window.addEventListener('popstate',()=>switchTab(sectionFromUrl(),false));init();
$('scheduleRoleFilter').addEventListener('change',()=>{renderScheduleSegmentation(true);renderSchedule()});$('scheduleEmployeeFilter').addEventListener('change',renderSchedule);
$('hrServiceSelect').addEventListener('change',event=>{activeHrService=event.target.value;if(activeHrSection==='onboarding')loadEmployees();else if(hrSchemas[activeHrSection])loadHrRecords(activeHrSection)});$('addHrService').addEventListener('click',addHrService);
$('showScheduleEmployeeForm').addEventListener('click',showScheduleEmployeeForm);$('closeScheduleEmployeeForm').addEventListener('click',()=>$('scheduleEmployeeForm').hidden=true);$('scheduleEmployeeForm').addEventListener('submit',submitScheduleEmployee);
$('showBulkScheduleForm').addEventListener('click',showBulkScheduleForm);$('closeBulkScheduleForm').addEventListener('click',()=>$('scheduleBulkForm').hidden=true);$('bulkScheduleActivity').addEventListener('change',toggleBulkShift);$('scheduleBulkForm').addEventListener('submit',submitBulkSchedule);toggleBulkShift();
$('bulkScheduleRole').addEventListener('change',renderBulkEmployees);
$('downloadScheduleImage').addEventListener('click',downloadScheduleImage);
$('createScheduleMonth').addEventListener('click',createScheduleMonth);
$('refreshVacationCapacity').addEventListener('click',refreshVacationCapacity);
$('scheduleEditEmployeeForm').addEventListener('submit',submitScheduleEditEmployee);$('closeScheduleEditEmployeeForm').addEventListener('click',()=>$('scheduleEditEmployeeForm').hidden=true);
document.querySelectorAll('[data-schedule-section]').forEach(button=>button.addEventListener('click',()=>switchScheduleSection(button.dataset.scheduleSection)));
