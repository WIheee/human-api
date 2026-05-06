        const { useState, useEffect, useRef, useCallback, createContext, useContext } = React;

        function statusLabel(s) { const map = { waiting:'等待回复', replied:'已回复', timeout:'已超时' }; return map[s]||s; }
        function formatTime(iso) { if(!iso) return ''; const d=new Date(iso); const pad=n=>n<10?'0'+n:n; return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }

        const ToastContext = createContext();
        function ToastProvider({ children }){
            const [toasts, setToasts] = useState([]);
            const addToast = useCallback((msg, type='info')=>{
                const id = Date.now()+Math.random();
                setToasts(p=>[...p, {id,msg,type}]);
                setTimeout(()=>setToasts(p=>p.filter(t=>t.id!==id)), 3500);
            },[]);
            return (
                <ToastContext.Provider value={addToast}>
                    {children}
                    <div style={{position:'fixed',bottom:100,right:20,zIndex:9999,display:'flex',flexDirection:'column',gap:10}}>
                        {toasts.map(t=>(
                            <div key={t.id} className="glass-heavy" style={{padding:'10px 20px',borderRadius:'var(--radius-lg)',fontWeight:600,color:'#fff',fontSize:14,animation:'bubbleIn 0.3s'}}>
                                {t.msg}
                            </div>
                        ))}
                    </div>
                </ToastContext.Provider>
            );
        }

        function App(){
            const [sessions, setSessions] = useState({});
            const [selectedSid, setSelectedSid] = useState(null);
            const [messages, setMessages] = useState([]);
            const [config, setConfig] = useState({ timeout:120, timeout_reply:'', api_key:''});
            const [wsConnected, setWsConnected] = useState(false);
            const [sidebarOpen, setSidebarOpen] = useState(window.innerWidth>=768);
            const [settingsOpen, setSettingsOpen] = useState(false);
            const [viewMode, setViewMode] = useState('list');
            const socketRef = useRef(null);
            const toast = useContext(ToastContext);
            const isDesktop = window.innerWidth>=768;

            useEffect(()=>{
                const h=()=>{ const d=window.innerWidth>=768; setSidebarOpen(d); if(d) setViewMode('list'); };
                window.addEventListener('resize',h);
                return ()=>window.removeEventListener('resize',h);
            },[]);

            useEffect(()=>{
                const s=io(window.location.origin,{ transports:['websocket', 'polling'], reconnection:true, reconnectionDelay:2000 });
                socketRef.current=s;
                s.on('connect',()=>setWsConnected(true));
                s.on('disconnect',()=>setWsConnected(false));
                s.on('init_data',data=>{
                    if(data.sessions){ const m={}; data.sessions.forEach(s=>m[s.id]=s); setSessions(m); }
                    if(data.config) setConfig(p=>({...p,...data.config}));
                });
                s.on('new_request',data=>{
                    setSessions(p=>({...p,[data.session.id]:data.session}));
                    toast(`新消息: ${(data.query_preview||'').substring(0,40)}...`, 'warning');
                });
                s.on('session_updated',data=>{
                    setSessions(p=>({...p,[data.id]:data}));
                    if(selectedSid===data.id&&data.messages) setMessages(data.messages);
                });
                s.on('sessions_list',data=>{ const m={}; (data.sessions||[]).forEach(s=>m[s.id]=s); setSessions(m); });
                s.on('messages_data',data=>{ if(data.session_id===selectedSid) setMessages(data.messages||[]); });
                return ()=>s.disconnect();
            },[selectedSid]);

            const selectSession = useCallback(sid=>{
                setSelectedSid(sid);
                const sess = sessions[sid];
                if(sess?.messages) setMessages(sess.messages);
                else if(sid) socketRef.current?.emit('request_messages', {session_id:sid});
                if(!isDesktop) setViewMode('chat');
            },[sessions,isDesktop]);

            const backToList = ()=>{ setSelectedSid(null); setMessages([]); setViewMode('list'); };

            const sendReply = useCallback(async content=>{
                if(!selectedSid||!content.trim()) return;
                try {
                    const res = await fetch('/api/admin/reply',{ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:selectedSid, content:content.trim()}) });
                    const data = await res.json();
                    if(data.success) toast('回复已发送','success');
                    else toast(data.error||'发送失败','error');
                } catch(err){ toast('网络错误: '+err.message,'error'); }
            },[selectedSid,toast]);

            const updateConfig = useCallback(async updates=>{
                try {
                    const res = await fetch('/api/admin/config',{ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(updates) });
                    const data = await res.json();
                    toast(data.success?'设置已保存':(data.error||'保存失败'), data.success?'success':'error');
                } catch(err){ toast('网络错误: '+err.message,'error'); }
            },[toast]);

            const clearSessions = useCallback(async()=>{
                if(!confirm('确定清空所有会话历史？')) return;
                try {
                    const res = await fetch('/api/admin/clear',{ method:'POST' });
                    const data = await res.json();
                    if(data.success){ setSessions({}); setSelectedSid(null); setMessages([]); toast('所有会话已清空','success'); }
                    else toast(data.error||'操作失败','error');
                } catch(err){ toast('网络错误: '+err.message,'error'); }
            },[toast]);

            const sessionList = Object.values(sessions).sort((a,b)=>{
                if(a.status==='waiting'&&b.status!=='waiting') return -1;
                if(a.status!=='waiting'&&b.status==='waiting') return 1;
                return (b.created_at||'').localeCompare(a.created_at||'');
            });
            const pending = sessionList.filter(s=>s.status==='waiting').length;
            const replied = sessionList.filter(s=>s.status==='replied').length;
            const current = selectedSid ? sessions[selectedSid] : null;

            return (
                <div style={{height:'100%',display:'flex',flexDirection:'column'}}>
                    <header className="navbar">
                        {isDesktop ? (
                            <button className="btn-icon" onClick={()=>setSidebarOpen(!sidebarOpen)}>
                                <i data-lucide="message-square" size="20"/>
                            </button>
                        ) : viewMode==='chat' ? (
                            <button className="btn-icon" onClick={backToList}>
                                <i data-lucide="arrow-left" size="20"/>
                            </button>
                        ) : (
                            <div style={{width:40}}/>
                        )}
                        <div style={{flex:1}} />
                        <div style={{display:'flex',alignItems:'center',gap:14}}>
                            <span title={wsConnected?'已连接':'已断开'} style={{width:8,height:8,borderRadius:'50%',background:wsConnected?'var(--green)':'var(--red)'}} />
                            <span title="待回复"><i data-lucide="clock" size="16"/> {pending}</span>
                            <span title="已回复"><i data-lucide="check-circle" size="16"/> {replied}</span>
                            <span title="总会话"><i data-lucide="message-square" size="16"/> {sessionList.length}</span>
                            <button className="btn-icon" onClick={()=>setSettingsOpen(!settingsOpen)}>
                                <i data-lucide="settings" size="20"/>
                            </button>
                        </div>
                    </header>

                    <div style={{display:'flex',flex:1,overflow:'hidden'}}>
                        {isDesktop && (
                            <nav className="glass-heavy" style={{
                                width: sidebarOpen?280:0, minWidth:sidebarOpen?280:0,
                                borderRight: sidebarOpen?'1px solid var(--glass-border)':'none',
                                overflowY:'auto', display:'flex', flexDirection:'column', transition:'width 0.35s ease'
                            }}>
                                {sidebarOpen && <SessionList sessions={sessionList} selectedSid={selectedSid} onSelect={selectSession} onRefresh={()=>socketRef.current?.emit('request_sessions')} />}
                            </nav>
                        )}

                        {!isDesktop && (
                            <div style={{flex:1,overflow:'hidden'}}>
                                {viewMode==='list' && <SessionList sessions={sessionList} selectedSid={selectedSid} onSelect={selectSession} onRefresh={()=>socketRef.current?.emit('request_sessions')} />}
                                {viewMode==='chat' && selectedSid && <ChatPanel session={current} messages={messages} onSend={sendReply} />}
                            </div>
                        )}

                        {isDesktop && (
                            <main style={{flex:1,background:'var(--bg-deep)',overflow:'hidden'}}>
                                {selectedSid ? <ChatPanel session={current} messages={messages} onSend={sendReply} /> : <EmptyChat />}
                            </main>
                        )}

                        {settingsOpen && (
                            <>
                                <div onClick={()=>setSettingsOpen(false)} style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.5)',zIndex:298}}></div>
                                <SettingsDrawer config={config} setConfig={setConfig} onSave={updateConfig} onClear={clearSessions} onClose={()=>setSettingsOpen(false)} />
                            </>
                        )}
                    </div>
                    <InitLucide deps={[messages,sessionList,sidebarOpen,settingsOpen,viewMode]} />
                </div>
            );
        }

        function SessionList({ sessions, selectedSid, onSelect, onRefresh }){
            return (
                <div style={{display:'flex',flexDirection:'column',height:'100%'}}>
                    <div className="glass-heavy" style={{padding:'14px',display:'flex',justifyContent:'space-between',alignItems:'center',flexShrink:0,borderBottom:'1px solid rgba(255,255,255,0.05)'}}>
                        <h3 style={{fontSize:14,fontWeight:600}}>会话列表</h3>
                        <button className="btn-icon" onClick={onRefresh}><i data-lucide="refresh-cw" size="18"/></button>
                    </div>
                    <div style={{flex:1,overflowY:'auto',padding:10}}>
                        {sessions.length===0 && <div style={{textAlign:'center',padding:24,color:'var(--text-secondary)'}}>暂无会话</div>}
                        {sessions.map((s,i)=>(
                            <div key={s.id} onClick={()=>onSelect(s.id)} className={`session-item ${selectedSid===s.id?'active':''}`} style={{animationDelay:`${i*0.04}s`}}>
                                <div style={{display:'flex',justifyContent:'space-between',marginBottom:6}}>
                                    <span style={{fontSize:11,color:'var(--text-secondary)',fontFamily:'monospace'}}>{s.id}</span>
                                    <span style={{
                                        fontSize:10,padding:'2px 10px',borderRadius:20,
                                        background: s.status==='waiting'?'rgba(240,178,50,0.2)': s.status==='replied'?'rgba(35,165,90,0.2)':'rgba(242,63,66,0.15)',
                                        color: s.status==='waiting'?'var(--yellow)': s.status==='replied'?'var(--green)':'var(--red)'
                                    }}>{statusLabel(s.status)}</span>
                                </div>
                                <div style={{fontSize:13,color:'var(--text-secondary)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
                                    {(() => {
                                        const raw = s.messages?.slice(-1)[0]?.content;
                                        const text = typeof raw === 'string' ? raw : JSON.stringify(raw || '');
                                        return text.substring(0, 40) || '(无消息)';
                                    })()}
                                </div>
                                <div style={{fontSize:11,color:'var(--text-secondary)',marginTop:4,opacity:0.6}}>
                                    {s.model} · {formatTime(s.created_at)}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            );
        }

        function ChatPanel({ session, messages, onSend }){
            const messagesEndRef = useRef(null);
            useEffect(()=>{ messagesEndRef.current?.scrollIntoView({ behavior:'smooth' }); },[messages]);
            if(!session) return null;
            const canReply = session.status==='waiting';

            return (
                <div style={{display:'flex',flexDirection:'column',height:'100%'}}>
                    <div style={{flex:1,overflowY:'auto',padding:'24px 28px'}}>
                        {messages.length===0 && <div style={{textAlign:'center',padding:40,color:'var(--text-secondary)'}}>暂无消息</div>}
                        {messages.map((msg,idx)=>{
                            const isUser = msg.role==='user';
                            const isAi = msg.role==='assistant';
                            const content = msg.content||'';
                            if(msg.role==='system') return (
                                <div key={idx} style={{display:'flex',justifyContent:'center',marginBottom:12}}>
                                    <div className="message-bubble bubble-system">{content}</div>
                                </div>
                            );
                            return (
                                <div key={idx} style={{display:'flex',justifyContent: isUser?'flex-start':'flex-end',marginBottom:14}}>
                                    <div className={`message-bubble ${isUser?'bubble-user':'bubble-ai'}`}>
                                        <div style={{fontSize:10,marginBottom:4,opacity:0.8,fontWeight:600}}>{isUser?'用户':'AI（你）'}</div>
                                        {content}
                                    </div>
                                </div>
                            );
                        })}
                        <div ref={messagesEndRef} />
                    </div>
                    <div className="glass-heavy" style={{padding:'16px 20px',flexShrink:0,borderTop:'1px solid rgba(255,255,255,0.06)'}}>
                        {canReply ? (
                            <div style={{display:'flex',gap:10,alignItems:'flex-end'}}>
                                <textarea
                                    id="reply-textarea"
                                    placeholder="输入回复... Ctrl+Enter 发送"
                                    rows={2}
                                    className="glass-input"
                                    style={{flex:1,resize:'vertical',fontFamily:'inherit',fontSize:14,minHeight:52}}
                                    onKeyDown={e=>{
                                        if(e.ctrlKey && e.key==='Enter'){
                                            e.preventDefault();
                                            document.getElementById('send-btn')?.click();
                                        }
                                    }}
                                />
                                <button id="send-btn" className="btn-send" onClick={()=>{
                                    const ta=document.getElementById('reply-textarea');
                                    if(ta?.value.trim()){
                                        onSend(ta.value);
                                        ta.value='';
                                    }
                                }}>
                                    <i data-lucide="send" size="20"/> 发送
                                </button>
                            </div>
                        ) : (
                            <div style={{textAlign:'center',color:'var(--text-secondary)',fontSize:13,padding:14}}>
                                该会话已{statusLabel(session.status)}，无法回复
                            </div>
                        )}
                    </div>
                </div>
            );
        }

        function EmptyChat(){
            return (
                <div style={{height:'100%',display:'flex',alignItems:'center',justifyContent:'center',flexDirection:'column',color:'var(--text-secondary)'}}>
                    <i data-lucide="messages-square" size="72" style={{opacity:0.2}}/>
                    <p style={{marginTop:16,fontSize:15}}>选择一个会话查看消息</p>
                </div>
            );
        }

        function SettingsDrawer({ config, setConfig, onSave, onClear, onClose }){
            const [local, setLocal] = useState({...config});
            useEffect(()=>setLocal({...config}),[config]);
            const save = ()=>onSave(local);

            return (
                <div className="glass-heavy" style={{
                    position:'fixed',right:0,top:0,bottom:0,width:340,maxWidth:'88vw',
                    borderLeft:'1px solid var(--glass-border)',zIndex:299,overflowY:'auto',padding:24,
                    animation:'slideInLeft 0.25s ease-out'
                }}>
                    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
                        <h3 style={{fontSize:16,fontWeight:700}}>系统设置</h3>
                        <button className="btn-icon" onClick={onClose}><i data-lucide="x" size="22"/></button>
                    </div>
                    <div style={{display:'flex',flexDirection:'column',gap:18}}>
                        <div>
                            <label style={{fontSize:12,fontWeight:600,color:'var(--text-secondary)',marginBottom:6,display:'block'}}>API 访问密钥</label>
                            <input type="text" value={local.api_key||''} onChange={e=>setLocal({...local,api_key:e.target.value})} placeholder="留空则不鉴权" className="glass-input" style={{width:'100%'}} />
                        </div>
                        <div>
                            <label style={{fontSize:12,fontWeight:600,color:'var(--text-secondary)',marginBottom:6,display:'block'}}>超时（秒）</label>
                            <input type="number" value={local.timeout} onChange={e=>setLocal({...local,timeout:parseInt(e.target.value)||120})} min="10" className="glass-input" style={{width:'100%'}} />
                        </div>
                        <div>
                            <label style={{fontSize:12,fontWeight:600,color:'var(--text-secondary)',marginBottom:6,display:'block'}}>超时默认回复</label>
                            <textarea rows="3" value={local.timeout_reply||''} onChange={e=>setLocal({...local,timeout_reply:e.target.value})} className="glass-input" style={{width:'100%',resize:'vertical',fontFamily:'inherit'}} />
                        </div>
                        <button className="btn-send" onClick={save} style={{width:'100%',justifyContent:'center'}}>保存设置</button>
                        <hr style={{borderColor:'rgba(255,255,255,0.05)'}}/>
                        <button className="btn-danger" onClick={onClear}>清空所有会话历史</button>
                    </div>
                </div>
            );
        }

        function InitLucide({ deps }){
            useEffect(()=>{ if(window.lucide) lucide.createIcons(); },[deps]);
            return null;
        }

        const root = ReactDOM.createRoot(document.getElementById('root'));
        root.render(<ToastProvider><App /></ToastProvider>);
