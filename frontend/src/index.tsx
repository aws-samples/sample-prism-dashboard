import React from 'react';
import ReactDOM from 'react-dom/client';
import { Amplify } from 'aws-amplify';
import '@cloudscape-design/global-styles/index.css';
import App from './App';
import { loadConfig } from './config';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

loadConfig().then((config) => {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
        identityPoolId: config.identityPoolId,
        loginWith: {
          oauth: {
            domain: config.oauthDomain,
            scopes: ['openid', 'email', 'profile'],
            redirectSignIn: [config.redirectUrl],
            redirectSignOut: [config.redirectUrl],
            responseType: 'code',
          },
        },
      },
    },
  });

  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
