# BrandAI EC2 GPU worker

The controller owns the loopback API, local brand databases, brand assets, queue and delivery credentials. A single controller queue process leases a private, disposable `g7.2xlarge` instance for GPU work. The instance has one RTX PRO 4500 Blackwell GPU with 32 GB of GPU memory. Choose `us-east-2` or `us-west-2`. Check the instance offering for the launch template's Availability Zone and request the applicable On-Demand GPU vCPU quota before deployment. [AWS G7 specifications](https://aws.amazon.com/ec2/instance-types/g7/), [EC2 instance offerings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html)

## Bake the AMI

Use a dedicated Linux x86_64 builder from a pinned NVIDIA-capable Ubuntu 24.04 AMI. Your base image should already include the Blackwell-compatible NVIDIA driver and CUDA runtime matched to your pinned PyTorch/CUDA wheels, Python 3.12 with venv, and the system libraries required by the pinned Chromium distribution. Record the base AMI ID, OS and driver build in the release manifest. Bake in the chosen region and copy or bake separately for the second region.

Prepare a trusted, read-only staging tree. Keep it outside the Git checkout and include every file except `SHA256SUMS` in that lock file.

| Staged location | Input |
|---|---|
| `requirements.lock` and `wheels/` | Offline, fully hashed dependency lock and Linux x86_64 wheels for FastAPI, Playwright, NumPy, Pillow, CUDA PyTorch, Diffusers, Transformers, Accelerate and the pinned SAM 2 wheel. Include their transitive dependencies. |
| `revisions.json` | Object mapping the three model repositories from `licenses/model-manifest.json` to their exact revisions, plus `llama.cpp` and `sam2` keys for full source commit hashes. |
| `bin/llama-server` | CUDA build that accepts the structured JSON and thinking-disabled flags used by the worker gateway. |
| `models/llm/Qwen_Qwen3.5-9B-Q8_0.gguf` | Pinned Qwen GGUF from the approved artifact repository. |
| `models/flux2-klein-4b/` and `models/multilingual-e5-base/` | Dereferenced local snapshots matching the repository pins. |
| `models/sam2/sam2.1_hiera_{tiny,small}.pt` | Both official SAM 2.1 checkpoints pinned by SHA-256 in the stage lock. |
| `fonts/` and `playwright/` | All five font and license paths from the repository font manifest, and the Linux Playwright cache matching the pinned Playwright wheel. |

Run `sudo ./deploy/aws/ami_bootstrap.sh /path/to/stage` on the builder. The script checks the staged hashes and pins, installs from the offline wheelhouse, and enables the worker service and poweroff watchdog for the next boot. It uses `worker_gateway.py` and `pixel_runner.py` from the checkout being baked. Save the source commit and staging manifest alongside the AMI release.

Before promotion, test on a disposable private GPU instance. Verify CUDA startup, representative Qwen structured output, an image plate, a SAM alpha mask, VRAM recovery between subprocesses, and shutdown by controller and watchdog. Confirm the OS package set includes the required dynamic libraries for llama.cpp and Chromium. The macOS suite checks the controller and provider contracts with doubles. Hardware validation takes place on the disposable Linux instance.

## Controller and storage

1. Use a private subnet and security group that allows the configured worker port only from the controller security group. The GPU instance receives a private address and needs no inbound internet or SSH. Use the pinned launch template version with a private subnet, encrypted root volume, delete-on-termination, and no public IP. Limit access to the worker endpoint to the controller. Use independent transport protection if routing beyond that private segment.
2. Use an encrypted EBS volume mounted locally on the controller for `BRANDAI_RUNTIME_ROOT`, including SQLite, brand media, job records, local E5, fonts and Chromium. Keep one active controller on the volume and take controlled backups. The GPU worker uses its encrypted, disposable EBS root for the baked stack and local request scratch.
3. EFS is optional for shared, read-only model or artifact staging. S3 can hold private, versioned, encrypted build artifacts and encrypted backups. Keep live SQLite and brand assets on the controller's locally mounted volume. The domain services do not depend on EFS or S3 for requests.
4. Install the controller package with `.[aws]`, install its local browser, fonts and E5 snapshot, and load the environment based on `.env.aws.example`. Run the API on loopback and one dedicated `brandai-jobs` process with the same runtime root. With `BRANDAI_CONTENT_PROVIDER=aws`, the API accepts queued content and visual jobs. The dedicated worker owns EC2 and job ticks. Run `brandai-jobs --once` for a controlled tick or `brandai-jobs --poll-seconds 3` as a supervised service.

## IAM and cost guardrails

- Give the controller role `ec2:RunInstances` for the exact launch template resources. Scope AMI, subnet, security groups, encrypted volumes and network interfaces to their provisioned resources, and constrain instance creation to `g7.2xlarge` and the required `BrandAIWorker` and `TerminateAfter` request tags. Use `ec2:CreateTags` only for tags during `RunInstances`. Grant `ec2:DescribeInstances` for health polling and `ec2:TerminateInstances` only for instances tagged with the controller's deployment value. If the launch template names an instance profile, restrict `iam:PassRole` to that exact role and `ec2.amazonaws.com`. If encrypted volumes use a customer-managed KMS key, authorize that key only for the relevant EC2/EBS operations. Test allowed and denied launches with EC2 dry-run as a separate deployment check. [RunInstances API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_RunInstances.html), [EC2 identity-based policies](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-policies-for-amazon-ec2.html)
- The controller requests exactly one On-Demand instance, requires IMDSv2 and sets instance-initiated shutdown to terminate. A stable client token is reused if a launch response is ambiguous. Default startup deadline is five minutes, idle drain is 90 seconds, and the controller's batch wall-time cap is one hour. It serializes GPU leases and turns over the instance after the batch expires.
- Set the independent boot watchdog above the controller cap with a small buffer for cleanup. The default watchdog powers off after 80 minutes. Configure an independent account-side check for the deployment's `TerminateAfter` tag, including orphaned instances that never booted successfully. Use an AWS Budget and alert on concurrent tagged workers or a stuck startup. Match one-worker limits to the controller's deliberately single-process deployment.
- Use a separate build role to read build artifacts. Steady-state workers need read-only access only to any explicitly provisioned staged artifacts. Keep OAuth keys and SMTP secrets on the controller.

## Official delivery configuration

- Set Meta Graph and LinkedIn API versions supported by your own approved app. Scope connection records to the intended Instagram professional account, Facebook Page, or LinkedIn member or organization. Instagram requires an injected `PublicMediaURLProvider` that makes only the approved JPEG temporarily reachable by Meta. LinkedIn carousel deliveries use the verified PDF as a document post. [Instagram media](https://developers.facebook.com/docs/graph-api/reference/shadow-ig-user/media/), [Facebook Page Photos](https://developers.facebook.com/docs/graph-api/reference/page/photos/), [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- Install a Fernet key via controller secrets and keep it consistent across the API and worker. The one-use state endpoint supports an OAuth integration that handles provider authorization and supplies the resulting tokens. LinkedIn refresh can use provisioned client credentials when the app's grant supports refresh. Inject the appropriate Meta token-renewal hook for the account's grant type. Configure TLS SMTP with `implicit` or `starttls`.
- When a dispatch reports `NEEDS_RECONCILIATION`, compare the provider's account history and request details before making any manual decision. The queue preserves the dispatch record and prevents automatic resubmission of a possibly successful post.
