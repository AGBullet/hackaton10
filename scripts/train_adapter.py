"""Optional offline LoRA runner. Requires a separately provisioned training environment.

Not executed on the current corpus: expert-approved GOLD is insufficient.
Test split is never loaded into the trainer; adapter is never auto-published.
"""
import argparse,json,hashlib,os
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',type=Path,required=True);parser.add_argument('--model',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();manifest=json.loads((args.dataset/'manifest.json').read_text(encoding='utf-8'))
    content=(args.dataset/'verified.jsonl').read_bytes()
    if not manifest['training_allowed'] or hashlib.sha256(content).hexdigest()!=manifest['sha256']:raise ValueError('GOLD gate / dataset hash mismatch')
    if not args.model.is_dir():raise ValueError('Use a local Transformers model directory, not a GGUF file')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM,Trainer,TrainingArguments,set_seed
    from peft import LoraConfig,get_peft_model
    set_seed(42)
    tokenizer=AutoTokenizer.from_pretrained(args.model,local_files_only=True,trust_remote_code=False)
    if tokenizer.pad_token is None:tokenizer.pad_token=tokenizer.eos_token
    data={'train':[],'validation':[]}
    for line in content.decode('utf-8').splitlines():
        r=json.loads(line)
        if r['split'] not in data:continue
        if not r.get('expert_verified') or not r.get('training_consent'):raise ValueError('Missing expert attestation')
        prompt=json.dumps({'parameter':r['parameter_code'],'entity':r['entity'],'sources':[{'stage':e['stage'],'quote':e['quote']} for e in r['evidence_snapshot']]},ensure_ascii=False)
        answer=json.dumps({'expected_value':r['expected_value'],'actual_value':r['actual_value'],'expert_label':r['status']},ensure_ascii=False)
        messages=[{'role':'user','content':prompt}]
        prefix=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        full=prefix+answer+tokenizer.eos_token
        tokens=tokenizer(full,truncation=True,max_length=1024,padding='max_length',add_special_tokens=False)
        prefix_length=len(tokenizer(prefix,add_special_tokens=False)['input_ids'])
        tokens['labels']=[-100 if i<prefix_length or not mask else t for i,(t,mask) in enumerate(zip(tokens['input_ids'],tokens['attention_mask']))]
        if all(t==-100 for t in tokens['labels']):continue
        data[r['split']].append(tokens)
    if not data['train'] or not data['validation']:raise ValueError('No usable train/validation examples after tokenization')
    model=AutoModelForCausalLM.from_pretrained(args.model,local_files_only=True,trust_remote_code=False,torch_dtype=torch.float16)
    model=get_peft_model(model,LoraConfig(r=8,lora_alpha=16,lora_dropout=.05,target_modules=['q_proj','v_proj'],task_type='CAUSAL_LM'))
    model.enable_input_require_grads()
    model.config.use_cache=False
    arguments=TrainingArguments(output_dir=str(args.output),num_train_epochs=1,per_device_train_batch_size=1,per_device_eval_batch_size=1,gradient_accumulation_steps=8,gradient_checkpointing=True,fp16=True,learning_rate=2e-4,eval_strategy='epoch',save_strategy='epoch',seed=42,report_to=[],dataloader_num_workers=0)
    trainer=Trainer(model=model,args=arguments,train_dataset=data['train'],eval_dataset=data['validation'])
    trainer.train();model.save_pretrained(args.output);tokenizer.save_pretrained(args.output)
    (args.output/'provenance.json').write_text(json.dumps({'dataset_sha256':manifest['sha256'],'seed':42,'model_path':str(args.model),'trained':len(data['train']),'validation':len(data['validation']),'test_used':False,'publication_allowed':False},indent=2),encoding='utf-8')

if __name__=='__main__':main()
