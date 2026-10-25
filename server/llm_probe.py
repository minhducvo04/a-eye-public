"""Run with: python -m server.llm_probe 'Your question'"""
import argparse

from server.llm import LLMClient


def main():
    parser = argparse.ArgumentParser(description='Probe the configured reasoning provider.')
    parser.add_argument('question')
    args = parser.parse_args()
    result = LLMClient().complete('Answer the question briefly.', args.question)
    print(f'provider={result.provider} model={result.model} ms={result.ms:.1f}')
    print(result.text)


if __name__ == '__main__':
    main()
