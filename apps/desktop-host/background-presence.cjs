'use strict';
const {t,resolveLocale}=require('../../packages/i18n/index.cjs');
const {DISPLAY_NAME,brandPNG}=require('./product-brand.cjs');

// Build-time SVG rendering is hash-bound; runtime only reads approved bytes.
// No geometry parser, image library, process, font or network is needed here.
function trayIconPNG(){return brandPNG('tray32');}

class BackgroundPresence{
  constructor({Tray,Menu,nativeImage,locale='zh-CN',getWindow,getOrb,reveal,onExit,isClosing=()=>false,onError=()=>{}}){
    Object.assign(this,{Menu,getWindow,getOrb,reveal,onExit,isClosing,onError});this.locale=resolveLocale(locale,locale);this.disposed=false;this.userBackground=false;
    const image=nativeImage.createFromBuffer(trayIconPNG());if(image.isEmpty())throw new Error(t('common','tray.icon_unavailable',this.locale,{product:DISPLAY_NAME}));
    this.tray=new Tray(image);
    try{
      this.setLocale(this.locale);this.tray.on('double-click',()=>this.restore());
      const window=getWindow();
      this.close=event=>{event.preventDefault();this.hide();};
      this.minimize=()=>{this.userBackground=true;this.syncOrb();};
      this.hidden=()=>{this.userBackground=true;this.syncOrb();};
      this.shown=()=>{this.userBackground=false;this.syncOrb();};
      window.on('close',this.close);window.on('minimize',this.minimize);window.on('hide',this.hidden);
      window.on('show',this.shown);window.on('restore',this.shown);
      this.window=window;
    }catch(error){this.tray.destroy();throw error;}
  }
  setLocale(locale){
    this.locale=resolveLocale(locale,locale);if(this.disposed)return {locale:this.locale};
    this.tray.setToolTip(t('common','tray.tooltip',this.locale,{product:DISPLAY_NAME}));
    this.menu=this.Menu.buildFromTemplate([{label:t('common','tray.open',this.locale,{product:DISPLAY_NAME}),click:()=>this.restore()},{type:'separator'},
      {label:t('common','tray.exit',this.locale,{product:DISPLAY_NAME}),click:()=>{void Promise.resolve().then(()=>this.onExit()).catch(error=>this.onError(error));}}]);
    this.tray.setContextMenu(this.menu);return {locale:this.locale};
  }
  syncOrb(){
    const window=this.getWindow(),orb=this.getOrb();if(this.disposed||!window||window.isDestroyed()||!orb)return;
    void Promise.resolve(orb.setHostMinimized(window.isMinimized()||!window.isVisible())).catch(error=>this.onError(error));
  }
  canAutoReveal(){return !this.disposed&&!this.userBackground&&!this.isClosing();}
  hide(){
    const window=this.getWindow();if(this.disposed||this.isClosing()||!window||window.isDestroyed())return false;
    this.userBackground=true;window.hide();this.syncOrb();return true;
  }
  restore(){
    const window=this.getWindow();if(this.disposed||this.isClosing()||!window||window.isDestroyed())return false;
    this.userBackground=false;this.reveal(window);this.syncOrb();return true;
  }
  dispose(){
    if(this.disposed)return;this.disposed=true;
    for(const [name,handler]of [['close',this.close],['minimize',this.minimize],['hide',this.hidden],['show',this.shown],['restore',this.shown]])this.window.removeListener(name,handler);
    this.tray.destroy();
  }
}
module.exports={BackgroundPresence,trayIconPNG};
