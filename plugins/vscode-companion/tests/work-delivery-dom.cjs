'use strict';
// Limited UNIT DOM only; no browser layout/theme/native evidence.
const assert=require('node:assert/strict');
class Text{constructor(value){this.nodeType=3;this.nodeValue=value;this.parentElement=null;}get textContent(){return this.nodeValue;}get nextSibling(){return this.parentElement?.childNodes[this.parentElement.childNodes.indexOf(this)+1]??null;}}
class Element{
 constructor(tag){this.nodeType=1;this.tagName=tag.toUpperCase();this.childNodes=[];this.attrs=new Map();this.listeners={};this.scrollTop=0;this.open=false;this.checked=false;this.disabled=false;this.value='';}
 get disabled(){return this.hasAttribute('disabled');}set disabled(value){if(value)this.setAttribute('disabled','');else this.removeAttribute('disabled');}
 get attributes(){return [...this.attrs].map(([name,value])=>({name,value}));}setAttribute(k,v){this.attrs.set(k,String(v));}getAttribute(k){return this.attrs.get(k)??null;}hasAttribute(k){return this.attrs.has(k);}removeAttribute(k){this.attrs.delete(k);}
 get firstChild(){return this.childNodes[0]??null;}get lastChild(){return this.childNodes.at(-1)??null;}get nextSibling(){return this.parentElement?.childNodes[this.parentElement.childNodes.indexOf(this)+1]??null;}
 get textContent(){return this.childNodes.map(n=>n.textContent).join('');}set textContent(value){this.childNodes=[];if(value!=='')this.append(new Text(String(value)));}
 append(...rows){for(const row of rows)this.insertBefore(row,null);}insertBefore(row,before){row.parentElement?.removeChild(row);const index=before===null?this.childNodes.length:this.childNodes.indexOf(before);assert(index>=0);this.childNodes.splice(index,0,row);row.parentElement=this;return row;}removeChild(row){this.childNodes.splice(this.childNodes.indexOf(row),1);row.parentElement=null;return row;}
 addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}fire(name,event={}){for(const fn of this.listeners[name]??[])fn(event);}matches(selector){return selector.split(',').some(part=>{const [tag,attr]=part.trim().split('[');return this.tagName===tag.toUpperCase()&&(!attr||this.hasAttribute(attr.slice(0,-1)));});}closest(selector){return this.matches(selector)?this:this.parentElement?.closest(selector);}
 querySelectorAll(selector){return this.childNodes.flatMap(row=>row.nodeType===1?[...(row.matches(selector)?[row]:[]),...row.querySelectorAll(selector)]:[]);}querySelector(selector){return this.querySelectorAll(selector)[0]??null;}
}

module.exports={Text,Element};
