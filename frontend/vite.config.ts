import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // CDK deploys from frontend/build (see cdk/lib/constructs/dashboard.ts)
    outDir: 'build',
  },
});
