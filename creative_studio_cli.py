import argparse, json
from creative_studio import CreativeStudio

p = argparse.ArgumentParser(description="MARK VII Creative Studio - NVIDIA NIM")
sub = p.add_subparsers(dest="cmd", required=True)
sub.add_parser("status")
img = sub.add_parser("image"); img.add_argument("prompt")
anim = sub.add_parser("animate"); anim.add_argument("image")
args = p.parse_args()
studio = CreativeStudio()
if args.cmd == "status": result = studio.status()
elif args.cmd == "image":
    result = studio.generate_image(args.prompt).__dict__
    if hasattr(studio.provider, "last_image_model"):
        result["model"] = studio.provider.last_image_model
else:
    result = studio.animate_image(args.image).__dict__
print(json.dumps(result, indent=2))
