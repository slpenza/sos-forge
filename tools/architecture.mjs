/* architecture v1.1.0 — shared from stevenpenza.com. A project's infrastructure as boxes and arrows: the AWS parts read from its synthesized CloudFormation, the rest from a short hand-written file (design/v0/v0.7.20.md). Copy it byte for byte; change it only in its home, as a new version.
 *
 *   node architecture.mjs --project <key> [--hand infra/architecture.hand.json] [--commit <sha>] [cdk.out]
 *
 * Writes the chart as JSON to stdout, and exits 1 naming each problem when
 * the hand file names a kind, a node or an edge end that isn't there. */

/** The chart format's version; a reader refuses any other. */
export const ARCHITECTURE_VERSION = 1;

// Each CloudFormation type that becomes a box, and the kind it is drawn as.
const TYPES = {
  'AWS::CloudFront::Distribution': 'cdn',
  'AWS::ApiGatewayV2::Api': 'api',
  'AWS::ApiGateway::RestApi': 'api',
  'AWS::Lambda::Function': 'function',
  'AWS::StepFunctions::StateMachine': 'workflow',
  'AWS::Events::Rule': 'schedule',
  'AWS::Scheduler::Schedule': 'schedule',
  'AWS::SES::ReceiptRule': 'mail',
  'AWS::SQS::Queue': 'queue',
  'AWS::SNS::Topic': 'topic',
  'AWS::DynamoDB::Table': 'table',
  'AWS::DynamoDB::GlobalTable': 'table',
  'AWS::S3::Bucket': 'bucket',
  'AWS::KMS::Key': 'key',
  'AWS::SecretsManager::Secret': 'secret',
  'AWS::SSM::Parameter': 'secret',
  'AWS::Cognito::UserPool': 'userpool',
};

/** Every kind a chart may hold: the generated ones, then the ones only a hand file adds. */
export const KINDS = [...new Set(Object.values(TYPES)), 'alarm', 'person', 'device', 'service', 'project', 'ci'];

// Trailing words a construct name repeats from its kind, dropped from its label.
const KIND_WORDS = /(?:\s(?:Fn|Function|Lambda|Table|Bucket|Api|Distribution|Topic|Queue|Rule|Key|Secret|Param|Parameter|Schedule))+$/;

/** The logical ids a CloudFormation value refers to, through Ref, Fn::GetAtt and Fn::Sub. Tested in architecture.test.mjs through every edge it finds. */
function refs(value, out = new Set()) {
  if (Array.isArray(value)) value.forEach((v) => refs(v, out));
  else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      if (k === 'Ref' && typeof v === 'string') out.add(v);
      else if (k === 'Fn::GetAtt') out.add(Array.isArray(v) ? v[0] : String(v).split('.')[0]);
      else if (k === 'Fn::Sub') {
        const s = Array.isArray(v) ? v[0] : v;
        for (const m of String(s).matchAll(/\$\{([A-Za-z0-9]+)(?:\.[A-Za-z0-9.]+)?\}/g)) out.add(m[1]);
        if (Array.isArray(v)) refs(v[1], out);
      } else refs(v, out);
    }
  }
  return out;
}

/** A resource's construct path below its stack, without the trailing Resource or Default, from aws:cdk:path; else its logical id less CDK's hash. Tested through every node's id and label. */
function segments(stack, logical, res) {
  const path = res.Metadata?.['aws:cdk:path'];
  if (!path) return [logical.replace(/[0-9A-F]{8}$/, '')];
  const parts = path.split('/');
  if (parts[0] === stack) parts.shift();
  while (parts.length > 1 && /^(Resource|Default)$/.test(parts[parts.length - 1])) parts.pop();
  return parts;
}

/** A readable label from construct names: words split, hashes dropped, and a trailing word that repeats the kind removed when anything is left. Tested in architecture.test.mjs: MetricsPublicFn reads Metrics Public. */
function label(parts) {
  const words = parts.join(' ').replace(/[0-9A-F]{8,}/g, '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/\s+/g, ' ').trim();
  return words.replace(KIND_WORDS, '').trim() || words;
}

/** The verbs an IAM action list amounts to: reads, writes, invokes, notifies, sends, signs or uses. Tested through the policy edges in architecture.test.mjs. */
function verbs(actions) {
  const out = new Set();
  for (const a of [].concat(actions || [])) {
    const [svc, op = '*'] = String(a).toLowerCase().split(':');
    if (svc === 'ses' || svc === 'sesv2') out.add('sends');
    else if (svc === 'lambda' && op.startsWith('invoke')) out.add('invokes');
    else if (svc === 'sns' && op.startsWith('publish')) out.add('notifies');
    else if (svc === 'states' && op.startsWith('start')) out.add('invokes');
    else if (svc === 'kms' && op === 'sign') out.add('signs');
    else if (/^(get|list|describe|query|scan|batchget|conditioncheck|head|receive)/.test(op)) out.add('reads');
    else if (/^(put|update|delete|batchwrite|abort|create|restore|send|change)/.test(op)) out.add('writes');
    else out.add('uses');
  }
  return out;
}

/**
 * The chart for one project: its stacks' resources as nodes, what refers to what as edges, merged with its hand file. Returns { doc, errors }; doc is null when there are errors. Tested in architecture.test.mjs against a fixture stack holding every kind of edge, and a hand file with each kind of mistake.
 * @param {{stack: string, template: object}[]} stacks synthesized templates, by stack name
 * @param {{project: string, hand?: object, commit?: string, at?: string}} opts
 */
export function toArchitecture(stacks, { project, hand = null, commit = null, at = null } = {}) {
  const nodes = new Map();
  const edges = new Map();
  const errors = [];

  const node = (id, kind, text, detail) => {
    if (!nodes.has(id)) nodes.set(id, { id, kind, label: text, ...(detail ? { detail } : {}) });
    return id;
  };
  const edge = (from, to, does, detail) => {
    if (!from || !to || from === to) return;
    const key = `${from}\u0000${to}`;
    const e = edges.get(key) || { from, to, does: new Set(), details: new Set() };
    [].concat(does).forEach((d) => e.does.add(d));
    if (detail) e.details.add(detail);
    edges.set(key, e);
  };

  for (const { stack, template } of stacks) {
    const res = template.Resources || {};
    const idOf = new Map();
    const nid = (logical) => idOf.get(logical);

    // CDK's own machinery is not the project's: a function a custom resource calls, and anything under Custom::.
    const internal = new Set();
    for (const [logical, r] of Object.entries(res)) {
      if (/^Custom::|^AWS::CloudFormation::CustomResource$/.test(r.Type)) refs(r.Properties?.ServiceToken).forEach((l) => internal.add(l));
      const path = r.Metadata?.['aws:cdk:path'] || '';
      if (/\/Custom::|\/AWS679f53fac002430cb0da5b7982bd2287|\/LogRetention|\/BucketNotificationsHandler/.test(path)) internal.add(logical);
    }

    for (const [logical, r] of Object.entries(res)) {
      const kind = TYPES[r.Type];
      if (!kind || internal.has(logical)) continue;
      const parts = segments(stack, logical, r);
      const p = r.Properties || {};
      let text = label(parts);
      let mailTo;
      if (r.Type === 'AWS::SES::ReceiptRule') {
        // Named by its construct, as Site Inbound; a generic Rule0 by its recipients' domains, never their addresses.
        const domains = [...new Set((p.Rule?.Recipients || []).map((a) => String(a).split('@').pop()))];
        const own = label(parts.slice(-1));
        mailTo = domains.length ? `to ${domains.join(', ')}` : undefined;
        text = /^Rule\s*\d*$/i.test(own) ? (domains.length ? `Mail ${mailTo}` : own) : own;
      }
      const kindOf = r.Type === 'AWS::Events::Rule' && !p.ScheduleExpression ? 'schedule' : kind;
      const detail = r.Type === 'AWS::Lambda::Function' ? p.Description
        : (mailTo || p.ScheduleExpression || p.Description || undefined);
      idOf.set(logical, node(`${stack}/${parts.join('/')}`, kindOf, text, detail && String(detail).slice(0, 160)));
    }

    // Which function each role belongs to, so a policy's grants become that function's edges.
    const fnOfRole = new Map();
    for (const [logical, r] of Object.entries(res)) {
      if (r.Type !== 'AWS::Lambda::Function' || !nid(logical)) continue;
      refs(r.Properties?.Role).forEach((role) => fnOfRole.set(role, [...(fnOfRole.get(role) || []), nid(logical)]));
    }
    const grant = (fns, statements) => {
      for (const s of [].concat(statements || [])) {
        if (s.Effect && s.Effect !== 'Allow') continue;
        const does = verbs(s.Action);
        if (does.has('sends')) fns.forEach((f) => edge(f, node(`${stack}/ses`, 'mail', 'Mail out'), 'sends'));
        does.delete('sends');
        if (!does.size) continue;
        for (const l of refs(s.Resource)) fns.forEach((f) => edge(f, nid(l), [...does]));
      }
    };
    for (const [logical, r] of Object.entries(res)) {
      const p = r.Properties || {};
      if (r.Type === 'AWS::IAM::Policy') {
        const fns = [...refs(p.Roles)].flatMap((role) => fnOfRole.get(role) || []);
        grant(fns, p.PolicyDocument?.Statement);
      } else if (r.Type === 'AWS::IAM::Role' && fnOfRole.has(logical)) {
        for (const pol of p.Policies || []) grant(fnOfRole.get(logical), pol.PolicyDocument?.Statement);
      }
    }

    // A function's environment naming a resource: it uses it, unless a grant already said how.
    for (const [logical, r] of Object.entries(res)) {
      if (r.Type !== 'AWS::Lambda::Function' || !nid(logical)) continue;
      for (const l of refs(r.Properties?.Environment?.Variables)) {
        const to = nid(l);
        if (to && !edges.has(`${nid(logical)}\u0000${to}`)) edge(nid(logical), to, 'uses');
      }
    }

    // HTTP APIs: each route to the function behind its integration, and the authorizer.
    const fnOfIntegration = new Map();
    for (const [logical, r] of Object.entries(res)) {
      if (r.Type === 'AWS::ApiGatewayV2::Integration') fnOfIntegration.set(logical, [...refs(r.Properties?.IntegrationUri)].map(nid).find(Boolean));
    }
    for (const r of Object.values(res)) {
      const p = r.Properties || {};
      const api = [...refs(p.ApiId ?? p.RestApiId)].map(nid).find(Boolean);
      if (r.Type === 'AWS::ApiGatewayV2::Route') {
        const fn = [...refs(p.Target)].map((l) => fnOfIntegration.get(l)).find(Boolean);
        edge(api, fn, 'routes', p.RouteKey);
      } else if (r.Type === 'AWS::ApiGatewayV2::Authorizer') {
        edge(api, [...refs(p.AuthorizerUri)].map(nid).find(Boolean), 'authorizes');
      } else if (r.Type === 'AWS::ApiGateway::Method') {
        edge(api, [...refs(p.Integration?.Uri)].map(nid).find(Boolean), 'routes', p.HttpMethod);
      }
    }

    for (const [logical, r] of Object.entries(res)) {
      const p = r.Properties || {};
      const from = nid(logical);
      if (r.Type === 'AWS::CloudFront::Distribution') {
        for (const o of p.DistributionConfig?.Origins || []) refs(o.DomainName).forEach((l) => edge(from, nid(l), 'serves'));
      } else if (r.Type === 'AWS::Events::Rule') {
        for (const t of p.Targets || []) refs(t.Arn).forEach((l) => edge(from, nid(l), 'triggers'));
      } else if (r.Type === 'AWS::Scheduler::Schedule') {
        refs(p.Target?.Arn).forEach((l) => edge(from, nid(l), 'triggers'));
      } else if (r.Type === 'AWS::SES::ReceiptRule') {
        for (const a of p.Rule?.Actions || []) {
          refs(a.S3Action?.BucketName).forEach((l) => edge(from, nid(l), 'stores', a.S3Action.ObjectKeyPrefix));
          refs(a.LambdaAction?.FunctionArn).forEach((l) => edge(from, nid(l), 'invokes'));
          refs(a.SNSAction?.TopicArn).forEach((l) => edge(from, nid(l), 'notifies'));
        }
      } else if (r.Type === 'AWS::Lambda::EventSourceMapping') {
        const fn = [...refs(p.FunctionName)].map(nid).find(Boolean);
        refs(p.EventSourceArn).forEach((l) => edge(nid(l), fn, 'triggers'));
      } else if (r.Type === 'AWS::SNS::Topic' || r.Type === 'AWS::SNS::Subscription') {
        const topic = r.Type === 'AWS::SNS::Topic' ? from : [...refs(p.TopicArn)].map(nid).find(Boolean);
        const subs = r.Type === 'AWS::SNS::Topic' ? p.Subscription || [] : [p];
        for (const s of subs) {
          if (String(s.Protocol).startsWith('email')) edge(topic, node(`${stack}/email`, 'mail', 'Email'), 'notifies');
          else refs(s.Endpoint).forEach((l) => edge(topic, nid(l), 'triggers'));
        }
      } else if (r.Type === 'AWS::CloudWatch::Alarm') {
        const targets = [...refs([p.AlarmActions, p.OKActions])].map(nid).filter(Boolean);
        if (!targets.length) continue;
        const alarms = node(`${stack}/alarms`, 'alarm', 'Alarms');
        nodes.get(alarms).count = (nodes.get(alarms).count || 0) + 1;
        targets.forEach((t) => edge(alarms, t, 'notifies'));
      }
    }
  }

  for (const a of nodes.values()) if (a.kind === 'alarm') a.label = `${a.count} alarm${a.count === 1 ? '' : 's'}`;

  if (hand) {
    if (hand.v !== ARCHITECTURE_VERSION) errors.push(`hand file: v is ${hand.v}, not ${ARCHITECTURE_VERSION}`);
    for (const n of hand.nodes || []) {
      if (!n.id || !n.label) errors.push(`hand file: a node needs an id and a label (${JSON.stringify(n.id ?? n.label ?? null)})`);
      else if (!KINDS.includes(n.kind)) errors.push(`hand file: node ${n.id} has kind ${JSON.stringify(n.kind)}, not one of ${KINDS.join(', ')}`);
      else if (nodes.has(n.id)) errors.push(`hand file: node ${n.id} is already in the chart`);
      else node(n.id, n.kind, n.label, n.detail);
    }
    for (const e of hand.edges || []) {
      for (const end of [e.from, e.to]) if (!nodes.has(end)) errors.push(`hand file: edge ${e.from} → ${e.to} names ${end}, which is not in the chart`);
      if (nodes.has(e.from) && nodes.has(e.to)) edge(e.from, e.to, e.label || 'uses', e.detail);
    }
  }
  if (errors.length) return { doc: null, errors };

  const sorted = [...nodes.values()].map(({ count, ...n }) => n).sort((a, b) => a.id.localeCompare(b.id));
  const lines = [...edges.values()].map((e) => ({
    from: e.from, to: e.to, label: [...e.does].sort().join(', '),
    ...(e.details.size ? { detail: [...e.details].sort().join(', ') } : {}),
  })).sort((a, b) => a.from.localeCompare(b.from) || a.to.localeCompare(b.to));
  return {
    doc: {
      v: ARCHITECTURE_VERSION, project, generatedAt: at, commit,
      stacks: stacks.map((s) => s.stack).sort(), nodes: sorted, edges: lines,
    },
    errors,
  };
}

/** The command line: reads cdk.out's templates and the hand file, prints the chart or the problems. Tested in architecture.test.mjs by running it on a fixture cdk.out. */
async function main(argv) {
  const { readFileSync, readdirSync } = await import('node:fs');
  const { join } = await import('node:path');
  const args = { dir: 'cdk.out' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--project' || a === '--hand' || a === '--commit') args[a.slice(2)] = argv[++i];
    else args.dir = a;
  }
  if (!args.project) {
    console.error('usage: node architecture.mjs --project <key> [--hand <file>] [--commit <sha>] [cdk.out]');
    return 2;
  }
  const stacks = readdirSync(args.dir).filter((f) => f.endsWith('.template.json')).sort()
    .map((f) => ({ stack: f.slice(0, -'.template.json'.length), template: JSON.parse(readFileSync(join(args.dir, f), 'utf8')) }));
  if (!stacks.length) {
    console.error(`${args.dir}: no *.template.json; run cdk synth first`);
    return 1;
  }
  const hand = args.hand ? JSON.parse(readFileSync(args.hand, 'utf8')) : null;
  const { doc, errors } = toArchitecture(stacks, { project: args.project, hand, commit: args.commit || null, at: new Date().toISOString() });
  if (errors.length) {
    errors.forEach((e) => console.error(e));
    return 1;
  }
  process.stdout.write(`${JSON.stringify(doc, null, 2)}\n`);
  return 0;
}

if (typeof process !== 'undefined' && process.argv?.[1]) {
  const { pathToFileURL } = await import('node:url');
  if (import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = await main(process.argv.slice(2));
}
