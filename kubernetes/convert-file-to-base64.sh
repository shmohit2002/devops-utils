#!/bin/bash

# Function to convert file to Base64
convert_to_base64() {
  input_file=$1
  output_file=$2

  if [ -f "$input_file" ]; then
    base64 "$input_file" > "$output_file"
    echo "File has been converted to Base64 and saved as $output_file"
  else
    echo "Input file does not exist."
  fi
}

# Example usage
input_file="kube-config.txt"
output_file="kube-config.b64"
convert_to_base64 "$input_file" "$output_file"
