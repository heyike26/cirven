import { render } from 'solid-js/web';
import App from './App';
import productBrand from './brand';
import {startUILanguage} from './ui-language';
import './styles.css';
import './native-collaboration.css';
import './agent-studio.css';
import './brand-and-spacing.css';
import './chat-workbench-layout.css';

const root=document.getElementById('root');
if(!root)throw new Error('AgentShell mount point missing');
document.title=productBrand.display_name;
const stopLanguage=startUILanguage();
window.addEventListener('pagehide',stopLanguage,{once:true});
render(()=> <App/>,root);
