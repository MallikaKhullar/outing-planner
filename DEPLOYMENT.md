# 🚀 Deployment Guide

Choose your platform below. **Railway is recommended** — it's the cheapest ($5-10/month), easiest to set up, and supports Python applications perfectly.

---

## 🎯 Quick Start (Railway - Recommended)

### Step 1: Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub (easiest)
3. Connect your GitHub account

### Step 2: Create New Project
1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Select your `outing-planner` repository
4. Click "Deploy"

### Step 3: Add Environment Variables
Railway will auto-detect your `requirements.txt` and `Procfile`. Now add your API keys:

1. Go to your Railway project
2. Click "Variables" tab
3. Add these environment variables:

```
ANTHROPIC_API_KEY=your_key_here
GOOGLE_PLACES_API_KEY=your_key_here
MAPBOX_TOKEN=your_key_here
OPENWEATHER_API_KEY=your_key_here (optional)
```

### Step 4: Deploy
- Railway auto-deploys when you push to GitHub
- Check the "Deployments" tab to see logs
- Your app will be live at a URL like: `https://outing-planner-production.up.railway.app`

### Estimated Cost
- **$5-10/month** for the compute
- Free tier available with limited usage
- Pay-as-you-go pricing after free credits

---

## 📦 Other Deployment Options

### Render (Similar to Railway)
**Cost:** Free tier available, ~$7/month for paid

1. Go to [render.com](https://render.com)
2. Create new "Web Service"
3. Connect your GitHub repo
4. Set environment variables in Settings
5. Deploy

We've included `render.yaml` in the repo for automatic configuration.

### Heroku
**Cost:** $7/month minimum (paid dynos only, free tier removed)

1. Install [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)
2. Run: `heroku create outing-planner`
3. Set environment variables:
   ```bash
   heroku config:set ANTHROPIC_API_KEY=your_key
   heroku config:set GOOGLE_PLACES_API_KEY=your_key
   heroku config:set MAPBOX_TOKEN=your_key
   ```
4. Deploy: `git push heroku main`

---

## 🔧 What's Already Configured

Your repo includes deployment-ready files:

- **`Procfile`** - Tells Heroku/Railway how to run your app
- **`render.yaml`** - Render-specific configuration
- **`runtime.txt`** - Specifies Python 3.11
- **`requirements.txt`** - All Python dependencies

---

## ✅ Pre-Deployment Checklist

Before deploying, make sure:

- [ ] `.env` file is in `.gitignore` (it is ✓)
- [ ] `MAPBOX_TOKEN` is in your `.env` (not hardcoded) ✓
- [ ] All API keys are set as environment variables (not in code) ✓
- [ ] Repository is pushed to GitHub ✓
- [ ] You have valid API keys for:
  - Anthropic Claude API
  - Google Places API
  - Mapbox
  - (Optional) OpenWeather API

---

## 🛠️ Troubleshooting

### App crashes on Railway/Render
Check the deployment logs:
- Railway: "Deployments" tab → click deployment → view logs
- Render: "Logs" tab on the right side

### "Module not found" errors
Your `requirements.txt` is already configured. If you see import errors:
- Verify all dependencies are in `requirements.txt`
- Redeploy

### "Mapbox token is blank"
Make sure you set the `MAPBOX_TOKEN` environment variable in your deployment platform's settings (not in the `.env` file).

### Port issues
The app listens on the PORT environment variable. Most platforms set this automatically to 8080 or similar. The `server.py` handles this:
```python
PORT = int(os.environ.get("PORT", 8080))
```

---

## 📊 Monitoring & Logs

### Railway
- Dashboard shows real-time metrics
- Click deployment to see full logs
- Built-in error tracking

### Render
- Logs tab shows live output
- Email alerts for crashes

### Heroku
```bash
heroku logs --tail
```

---

## 🔄 Updating Your Deployment

Simply push to GitHub and your app redeploys automatically:
```bash
git push origin main
```

That's it! The deployment platform watches your repo and redeploys on every push.

---

## 💡 Tips

1. **Start with Railway** - it's the easiest and cheapest
2. **Test locally first** - run `python server.py` before deploying
3. **Monitor initial deployment** - check logs to catch errors early
4. **Scale gradually** - start on free tier, upgrade if needed

---

## 🎉 You're Live!

Once deployed, your Outing Planner will be accessible at your app's public URL. Share it, test it, and enjoy!

Need help? Check the logs or open an issue on GitHub.
