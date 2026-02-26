import { defineConfig } from 'astro/config';

export default defineConfig({
  base: '/dashboard',
  output: 'static',
  build: {
    format: 'file',
  },
});
