import {splitProps} from 'solid-js';
import {ProviderIcon as UpstreamProviderIcon, type ProviderIconProps} from '../../../staging-native-ui/upstream/packages/ui/src/components/provider-icon';
import icon from '../../../packages/branding/icon.svg';

export function ProviderIcon(props:ProviderIconProps){
  const [local,rest]=splitProps(props,['id']);
  return local.id==='opencode'||local.id==='opencode-go'
    ? <svg data-component="provider-icon" viewBox="0 0 32 32" {...rest}><image href={icon} width="32" height="32"/></svg>
    : <UpstreamProviderIcon {...props}/>;
}
